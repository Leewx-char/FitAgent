"""中文检索评测集、最终 RRF 输出指标与架构文档测试。"""

from pathlib import Path

from app.evaluation.retrieval_evaluator import (
    ExpectedChunk,
    RetrievalEvaluationCase,
    RetrievalEvaluationReport,
    RetrievalEvaluator,
    assert_quality_gate,
    load_cases,
)
from app.services.knowledge_indexer import KnowledgeIndexer
from app.services.retrieval_contracts import RetrievalHit, RetrievalRequest, RetrievalResult
from app.utils.config_handler import get_vector_store_config
from app.utils.path_tool import get_abs_path


def _hit(source_id: str, chunk_id: str, rank: int) -> RetrievalHit:
    return RetrievalHit(
        evidence_id=f"{source_id}#{chunk_id}",
        source_id=source_id,
        chunk_id=chunk_id,
        text="受控知识库证据",
        rank=rank,
        score=1 / rank,
        metadata={},
    )


def _result_for(query: str) -> RetrievalResult:
    """构造最终 RRF 输出，而非 dense/BM25 中间排序。"""
    return RetrievalResult(
        request=RetrievalRequest(query=query),
        hits=(
            _hit("其他资料.txt", "other", 1),
            _hit("营养学知识.txt", "protein", 2),
        ),
        elapsed_ms=12,
    )


def test_evaluator_calculates_recall_and_mrr_from_final_hits():
    """只按最终输出的 source_id/chunk_id 与其最终顺序计算指标。"""
    cases = [
        RetrievalEvaluationCase(
            case_id="protein",
            category="精确术语",
            query="增肌蛋白质",
            expected_chunks=(ExpectedChunk("营养学知识.txt", "protein"),),
        ),
        RetrievalEvaluationCase(
            case_id="missing",
            category="同义表述",
            query="没命中的问题",
            expected_chunks=(ExpectedChunk("动作指南大全.txt", "squat"),),
        ),
    ]

    report = RetrievalEvaluator(_result_for, top_k=6).evaluate(cases)

    assert report.recall_at_k == 0.5
    assert report.mrr == 0.25
    assert report.cases[0].first_expected_rank == 2
    assert report.cases[1].first_expected_rank is None


def test_quality_gate_requires_recall_at_6_and_mrr_thresholds():
    """合并前基线同时要求 Recall@6 >= 0.90 和 MRR >= 0.70。"""
    passing = RetrievalEvaluationReport(top_k=6, recall_at_k=0.90, mrr=0.70, cases=())
    assert passing.recall_at_k >= 0.90
    assert passing.mrr >= 0.70
    assert_quality_gate(passing)

    below_recall = RetrievalEvaluationReport(top_k=6, recall_at_k=0.89, mrr=0.70, cases=())
    below_mrr = RetrievalEvaluationReport(top_k=6, recall_at_k=0.90, mrr=0.69, cases=())

    for report in (below_recall, below_mrr):
        try:
            assert_quality_gate(report)
        except RuntimeError:
            continue
        raise AssertionError("未达标的检索评测不应通过质量门槛")


def test_curated_chinese_evaluation_set_has_auditable_chunk_annotations():
    """人工标注集覆盖关键词、同义表述和混合 RRF 查询，并定位真实切片。"""
    cases = load_cases(get_abs_path("app/evaluation/retrieval_cases.json"))
    indexed_pairs = {
        (chunk.metadata["source_id"], chunk.chunk_id)
        for chunk in KnowledgeIndexer(initialize_repository=False).preflight()
    }

    assert len(cases) >= 20
    assert {case.category for case in cases} >= {"精确术语", "同义表述", "混合问题"}
    assert all(case.query and case.expected_chunks for case in cases)
    assert {
        (expected.source_id, expected.chunk_id)
        for case in cases
        for expected in case.expected_chunks
    } <= indexed_pairs


def test_minimal_hybrid_configuration_and_docs_match_runtime_contract():
    """配置、依赖和面向用户的操作说明不再提及已删除的旧管线。"""
    config = get_vector_store_config()
    assert config == {
        "backend": "qdrant",
        "url": "http://localhost:6333",
        "grpc_port": 6334,
        "prefer_grpc": True,
        "qdrant_timeout_seconds": 60,
        "collection_name": "fitagent_knowledge",
        "dense_vector_name": "dense",
        "sparse_vector_name": "sparse",
        "sparse_model": "Qdrant/bm25",
        "sparse_language": "chinese",
        "sparse_tokenizer": "multilingual",
        "bm25_avg_len": 400,
        "data_path": "data",
        "allow_knowledge_file_type": ["txt", "md", "pdf"],
        "chunk_size": 500,
        "chunk_overlap": 80,
        "batch_size": 32,
        "min_source_count": 1,
        "min_chunk_count": 1,
        "k": 6,
        "candidate_k": 15,
        "max_context_chars": 6000,
        "max_chars_per_evidence": 1200,
        "evaluation_cases_path": "app/evaluation/retrieval_cases.json",
    }

    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")
    guide = Path("docs/learning-guide.md").read_text(encoding="utf-8")
    assert "rank-bm25" not in pyproject
    assert "alembic" not in pyproject.lower()
    for document in (readme, guide):
        assert "knowledge_preflight" not in document
        assert "一次 Qdrant Query API" in document
        assert "Unicode" in document
    assert "会**重建** `fitagent_knowledge`" in readme
    assert "只创建缺失关系表，不重建知识库" in readme
