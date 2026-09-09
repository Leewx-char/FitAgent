"""受控中文知识库的最终检索结果质量评测。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from app.services.rag_service import RagSummarizeService
from app.services.retrieval_contracts import RetrievalResult
from app.utils.config_handler import get_vector_store_config
from app.utils.path_tool import get_abs_path

_RECALL_AT_6_THRESHOLD = 0.90
_MRR_THRESHOLD = 0.70


@dataclass(frozen=True)
class ExpectedChunk:
    """人工审核的相关来源切片标识。"""

    source_id: str
    chunk_id: str


@dataclass(frozen=True)
class RetrievalEvaluationCase:
    """一条人工审核过的检索期望。"""

    case_id: str
    category: str
    query: str
    expected_chunks: tuple[ExpectedChunk, ...]


@dataclass(frozen=True)
class CaseEvaluation:
    """单条最终检索输出的可审计评测结果。"""

    case_id: str
    category: str
    recalled: bool
    first_expected_rank: int | None
    retrieved_chunks: tuple[ExpectedChunk, ...]


@dataclass(frozen=True)
class RetrievalEvaluationReport:
    """只从最终 RRF 输出计算的聚合质量指标。"""

    top_k: int
    recall_at_k: float
    mrr: float
    cases: tuple[CaseEvaluation, ...]

    def to_dict(self) -> dict:
        """返回可直接写入 CI 日志或人工检查的 JSON 结构。"""
        return {
            "case_count": len(self.cases),
            f"recall_at_{self.top_k}": self.recall_at_k,
            "mrr": self.mrr,
            "cases": [asdict(case) for case in self.cases],
        }


def load_cases(path: str) -> list[RetrievalEvaluationCase]:
    """从受版本控制的 JSON 文件加载 source/chunk 人工标注。"""
    raw_cases = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = [
        RetrievalEvaluationCase(
            case_id=item["case_id"],
            category=item["category"],
            query=item["query"],
            expected_chunks=tuple(
                ExpectedChunk(
                    source_id=expected["source_id"],
                    chunk_id=expected["chunk_id"],
                )
                for expected in item["expected_chunks"]
            ),
        )
        for item in raw_cases
    ]
    if not cases:
        raise ValueError("检索评测集不能为空。")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("检索评测集存在重复 case_id。")
    if any(not case.query or not case.expected_chunks for case in cases):
        raise ValueError("每条检索评测必须包含问题和至少一个预期切片。")
    return cases


class RetrievalEvaluator:
    """以用户实际收到的最终证据顺序计算 Recall@K 与 MRR。"""

    def __init__(self, retrieve: Callable[[str], RetrievalResult], top_k: int = 6) -> None:
        self.retrieve = retrieve
        self.top_k = top_k

    def evaluate_case(self, case: RetrievalEvaluationCase) -> CaseEvaluation:
        """只检查最终 RRF 输出的前 ``top_k`` 条，而非任何中间候选或排序。"""
        result = self.retrieve(case.query)
        hits = result.hits[: self.top_k]
        expected_pairs = {(item.source_id, item.chunk_id) for item in case.expected_chunks}
        first_expected_rank = next(
            (
                rank
                for rank, hit in enumerate(hits, start=1)
                if (hit.source_id, hit.chunk_id) in expected_pairs
            ),
            None,
        )
        return CaseEvaluation(
            case_id=case.case_id,
            category=case.category,
            recalled=first_expected_rank is not None,
            first_expected_rank=first_expected_rank,
            retrieved_chunks=tuple(
                ExpectedChunk(source_id=hit.source_id, chunk_id=hit.chunk_id) for hit in hits
            ),
        )

    def evaluate(self, cases: list[RetrievalEvaluationCase]) -> RetrievalEvaluationReport:
        """执行整套评测并计算每题一次的 Recall@K 与 reciprocal rank。"""
        if not cases:
            raise ValueError("检索评测集不能为空。")
        details = tuple(self.evaluate_case(case) for case in cases)
        count = len(details)
        return RetrievalEvaluationReport(
            top_k=self.top_k,
            recall_at_k=round(sum(item.recalled for item in details) / count, 4),
            mrr=round(
                sum(1 / item.first_expected_rank for item in details if item.first_expected_rank)
                / count,
                4,
            ),
            cases=details,
        )


def assert_quality_gate(report: RetrievalEvaluationReport) -> None:
    """拒绝不满足合并前 Recall@6 与 MRR 基线的实际评测结果。"""
    if report.top_k != 6:
        raise RuntimeError(f"检索质量门槛固定为 Recall@6，当前 top_k={report.top_k}。")
    if report.recall_at_k < _RECALL_AT_6_THRESHOLD or report.mrr < _MRR_THRESHOLD:
        raise RuntimeError(
            "检索质量未达合并前基线："
            f"Recall@6={report.recall_at_k:.2%}（要求 >= {_RECALL_AT_6_THRESHOLD:.2%}），"
            f"MRR={report.mrr:.2%}（要求 >= {_MRR_THRESHOLD:.2%}）。"
        )


def main() -> None:
    """运行在线最终结果评测，并在标准输出提供可留存的 JSON 报告。"""
    config = get_vector_store_config()
    cases = load_cases(get_abs_path(config["evaluation_cases_path"]))
    service = RagSummarizeService()
    try:
        report = RetrievalEvaluator(service.retrieve, top_k=config["k"]).evaluate(cases)
        assert_quality_gate(report)
    except RuntimeError as error:
        raise SystemExit(
            "检索评测未通过：无法完成在线查询或未达到质量门槛。"
            f"\n原因：{error}"
            "\n请确认 Qdrant 已启动，并检查当前终端到 DashScope 的网络、代理和防火墙设置后重试。"
        ) from error
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    print(f"检索质量门槛通过：Recall@6={report.recall_at_k:.2%}，MRR={report.mrr:.2%}")


if __name__ == "__main__":
    main()
