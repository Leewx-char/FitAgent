"""中文检索评测集与指标计算测试。"""

from app.evaluation.retrieval_evaluator import (
    RetrievalEvaluationCase,
    RetrievalEvaluator,
    load_cases,
)
from app.services.retrieval_contracts import RetrievalHit, RetrievalRequest, RetrievalResult
from app.utils.path_tool import get_abs_path


def _result_for(query: str) -> RetrievalResult:
    return RetrievalResult(
        request=RetrievalRequest(query=query),
        expanded_query=query,
        search_queries=(query,),
        index_revision="revision-1",
        hits=(
            RetrievalHit(
                evidence_id="营养学知识.txt#1",
                source_id="营养学知识.txt",
                chunk_id="1",
                parent_id="parent-1",
                text="增肌人群建议蛋白质摄入 1.6-2.2g/kg。",
                child_text="蛋白质摄入 1.6-2.2g/kg。",
                rank=1,
                score=0.03,
                dense_rank=1,
                bm25_rank=1,
                rerank_score=0.9,
                metadata={},
            ),
        ),
        vector_candidate_count=1,
        bm25_candidate_count=1,
        elapsed_ms=12,
        bm25_enabled=True,
        query_planner_used_llm=False,
    )


def test_evaluator_calculates_source_and_evidence_metrics():
    case = RetrievalEvaluationCase(
        case_id="nutrition-protein",
        category="营养",
        query="增肌每天蛋白质吃多少？",
        expected_sources=("营养学知识.txt",),
        evidence_terms=("1.6-2.2g/kg",),
    )

    report = RetrievalEvaluator(_result_for, top_k=6).evaluate([case])

    assert report["recall_at_6"] == 1.0
    assert report["top1_source_accuracy"] == 1.0
    assert report["evidence_support_at_6"] == 1.0


def test_curated_chinese_evaluation_set_is_loadable_and_diverse():
    cases = load_cases(get_abs_path("app/evaluation/retrieval_cases.json"))

    assert len(cases) == 24
    assert {case.category for case in cases} >= {"动作", "营养", "防护"}
    assert all(case.expected_sources and case.evidence_terms for case in cases)
