"""中文知识库检索评测器。

评测只衡量检索证据是否出现，不调用 LLM 生成答案。因此结果可重复，也能直接用于
决定是否需要查询改写、扩大候选池或引入重排序。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.services.rag_service import RagSummarizeService
from app.services.retrieval_contracts import RetrievalResult
from app.utils.config_handler import get_vector_store_config
from app.utils.path_tool import get_abs_path


@dataclass(frozen=True)
class RetrievalEvaluationCase:
    """一条人工审核过的检索期望。"""

    case_id: str
    category: str
    query: str
    expected_sources: tuple[str, ...]
    evidence_terms: tuple[str, ...]


@dataclass(frozen=True)
class CaseEvaluation:
    """单条评测的可审计结果。"""

    case_id: str
    category: str
    source_recalled: bool
    top1_source_correct: bool
    evidence_supported: bool
    retrieved_evidence_ids: tuple[str, ...]
    retrieved_sources: tuple[str, ...]


def load_cases(path: str) -> list[RetrievalEvaluationCase]:
    """从受版本控制的 JSON 文件加载评测集，并校验其最小结构。"""

    raw_cases = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = [
        RetrievalEvaluationCase(
            case_id=item["case_id"],
            category=item["category"],
            query=item["query"],
            expected_sources=tuple(item["expected_sources"]),
            evidence_terms=tuple(item["evidence_terms"]),
        )
        for item in raw_cases
    ]
    if not cases:
        raise ValueError("检索评测集不能为空。")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("检索评测集存在重复 case_id。")
    return cases


class RetrievalEvaluator:
    """执行来源召回、Top-1 来源正确率与证据支持率评测。"""

    def __init__(self, retrieve: Callable[[str], RetrievalResult], top_k: int = 6) -> None:
        self.retrieve = retrieve
        self.top_k = top_k

    def evaluate_case(self, case: RetrievalEvaluationCase) -> CaseEvaluation:
        """评测一条问题，并仅检查前 ``top_k`` 个可引用证据。"""

        result = self.retrieve(case.query)
        hits = result.hits[: self.top_k]
        sources = tuple(hit.source_id for hit in hits)
        expected_sources = set(case.expected_sources)
        supporting_hits = [hit for hit in hits if hit.source_id in expected_sources]
        normalized_evidence = "\n".join(hit.text.lower() for hit in supporting_hits)
        evidence_supported = bool(supporting_hits) and all(
            term.lower() in normalized_evidence for term in case.evidence_terms
        )
        return CaseEvaluation(
            case_id=case.case_id,
            category=case.category,
            source_recalled=bool(expected_sources.intersection(sources)),
            top1_source_correct=bool(hits) and hits[0].source_id in expected_sources,
            evidence_supported=evidence_supported,
            retrieved_evidence_ids=tuple(hit.evidence_id for hit in hits),
            retrieved_sources=sources,
        )

    def evaluate(self, cases: list[RetrievalEvaluationCase]) -> dict:
        """执行整套评测并生成可比较的汇总指标。"""

        details = [self.evaluate_case(case) for case in cases]
        count = len(details)
        return {
            "case_count": count,
            f"recall_at_{self.top_k}": round(
                sum(item.source_recalled for item in details) / count, 4
            ),
            "top1_source_accuracy": round(
                sum(item.top1_source_correct for item in details) / count, 4
            ),
            f"evidence_support_at_{self.top_k}": round(
                sum(item.evidence_supported for item in details) / count, 4
            ),
            "cases": [asdict(item) for item in details],
        }


def main() -> None:
    """以当前 ``rag_active`` revision 运行评测，并写入忽略的运行报告。"""

    config = get_vector_store_config()
    cases = load_cases(get_abs_path(config["evaluation_cases_path"]))
    service = RagSummarizeService()
    try:
        report = RetrievalEvaluator(service.retrieve, top_k=config["k"]).evaluate(cases)
    except RuntimeError as error:
        raise SystemExit(
            "检索评测未完成：无法完成在线查询向量化或读取索引。"
            f"\n原因：{error}"
            "\n请确认 Qdrant 已启动，并检查当前终端到 DashScope 的网络、代理和防火墙设置后重试。"
        ) from error
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["index_revision"] = service.vector_store.active_revision()
    report["retrieval_config"] = {
        key: config.get(key)
        for key in (
            "k",
            "candidate_k",
            "query_planner_enabled",
            "reranker_enabled",
            "reranker_candidate_k",
            "reranker_base_score_weight",
            "source_quality_penalties",
            "metadata_tag_boost_enabled",
            "metadata_tag_boost_weight",
        )
    }
    report_path = Path(get_abs_path(config["evaluation_report_path"]))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    recall_key = f"recall_at_{config['k']}"
    evidence_key = f"evidence_support_at_{config['k']}"
    print(
        "检索评测完成："
        f"Recall@{config['k']}={report[recall_key]:.2%}，"
        f"Top-1 来源正确率={report['top1_source_accuracy']:.2%}，"
        f"证据支持率={report[evidence_key]:.2%}"
    )


if __name__ == "__main__":
    main()
