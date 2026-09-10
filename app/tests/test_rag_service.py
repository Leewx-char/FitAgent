"""原始查询单次检索、最终排名和引用契约测试。"""

from dataclasses import fields

import pytest
from langchain_core.documents import Document

from app.core.request_context import request_id_var
from app.services.context_builder import ContextBuilder
from app.services.rag_service import RagSummarizeService
from app.services.retrieval_contracts import RetrievalHit, RetrievalResult
from app.services.vector_repository import ScoredChunk


class CapturingVectorStore:
    """仅提供 native hybrid 接口，不加载工件或连接外部服务。"""

    def __init__(self, results=None):
        self.calls = []
        self.results = (
            results
            if results is not None
            else [
                ScoredChunk(
                    Document(
                        page_content="深蹲时膝盖应追踪脚尖方向。",
                        metadata={"source_id": "动作.md", "chunk_id": "squat", "ordinal": 7},
                    ),
                    0.923456789,
                ),
                ScoredChunk(
                    Document(
                        page_content="训练前建议进行动态热身。",
                        metadata={"source_id": "热身.md", "chunk_id": "warmup"},
                    ),
                    0.72,
                ),
            ]
        )

    def hybrid_search(self, query, *, limit, source_filter=()):
        """记录一次请求并原样返回 Qdrant 的排序结果。"""
        self.calls.append((query, limit, source_filter))
        return self.results

    @staticmethod
    def health():
        return {"status": "ready"}


class PassThroughReranker:
    """测试中不调用 DashScope，保留首阶段候选的既有顺序。"""

    @staticmethod
    def rerank(query, candidates, *, limit):
        return list(candidates)[:limit]


def make_service(store, **kwargs):
    return RagSummarizeService(vector_store=store, reranker=PassThroughReranker(), **kwargs)


def test_retrieve_delegates_original_query_once():
    store = CapturingVectorStore()

    result = make_service(store).retrieve("深蹲时膝盖怎么放？")

    assert store.calls == [("深蹲时膝盖怎么放？", 30, ())]
    assert result.hits[0].rank == 1
    assert result.hits[0].source_id == "动作.md"
    assert result.hits[0].evidence_id == "动作.md#squat"
    assert result.hits[0].text == "深蹲时膝盖应追踪脚尖方向。"
    assert [hit.score for hit in result.hits] == [0.923456789, 0.72]


def test_retrieve_reranks_thirty_first_stage_candidates_before_returning_top_six():
    class CapturingReranker:
        def __init__(self):
            self.calls = []

        def rerank(self, query, candidates, *, limit):
            self.calls.append((query, candidates, limit))
            return [
                ScoredChunk(candidates[1].document, 0.98),
                ScoredChunk(candidates[0].document, 0.77),
            ]

    store = CapturingVectorStore()
    reranker = CapturingReranker()

    result = RagSummarizeService(vector_store=store, reranker=reranker).retrieve("深蹲")

    assert store.calls == [("深蹲", 30, ())]
    assert reranker.calls == [("深蹲", store.results, 6)]
    assert [hit.chunk_id for hit in result.hits] == ["warmup", "squat"]
    assert [hit.score for hit in result.hits] == [0.98, 0.77]


def test_retrieve_preserves_query_and_source_filter():
    store = CapturingVectorStore()
    query = "  Squat 深蹲\n怎么做？  "

    result = make_service(store).retrieve(query, ["动作.md"])

    assert store.calls == [(query, 30, ("动作.md",))]
    assert result.request.query == query
    assert result.request.source_filter == ("动作.md",)


def test_retrieve_preserves_returned_order_without_local_deduplication():
    store = CapturingVectorStore()
    store.results = [store.results[1], store.results[0], store.results[0]]

    result = make_service(store).retrieve("深蹲")

    assert [hit.chunk_id for hit in result.hits] == ["warmup", "squat", "squat"]
    assert [hit.rank for hit in result.hits] == [1, 2, 3]
    assert [hit.score for hit in result.hits] == [0.72, 0.923456789, 0.923456789]


def test_retrieve_rejects_history():
    service = make_service(CapturingVectorStore())

    with pytest.raises(TypeError, match="history"):
        service.retrieve("深蹲", history=[])
    with pytest.raises(TypeError, match="history"):
        service.build_context("深蹲", history=[])


def test_retrieval_contract_contains_only_final_evidence_and_minimal_metrics():
    assert {field.name for field in fields(RetrievalHit)} == {
        "evidence_id",
        "source_id",
        "chunk_id",
        "text",
        "rank",
        "score",
        "metadata",
    }
    assert {field.name for field in fields(RetrievalResult)} == {
        "request",
        "hits",
        "elapsed_ms",
    }
    token = request_id_var.set("rag-request-1")
    try:
        result = make_service(CapturingVectorStore()).retrieve("深蹲")
    finally:
        request_id_var.reset(token)

    assert result.log_payload() == {
        "request_id": "rag-request-1",
        "query_length": 2,
        "selected": 2,
        "elapsed_ms": result.elapsed_ms,
    }
    assert result.elapsed_ms >= 0
    assert "深蹲" not in str(result.log_payload())


def test_build_context_includes_citable_evidence_markers():
    service = make_service(CapturingVectorStore())

    context = service.build_context("深蹲时膝盖怎么放？")

    assert "[证据:1]" in context.content
    assert "动作.md#squat" in context.content
    assert "回答若采用以上资料" in context.content
    assert context.result.hits[0].text in context.content


def test_build_context_respects_injected_builder_budget():
    service = make_service(
        CapturingVectorStore(),
        context_builder=ContextBuilder(max_context_chars=8, max_chars_per_evidence=8),
    )

    context = service.build_context("深蹲")

    assert "已按上下文预算截取" in context.content
    assert "训练前建议进行动态热身。" not in context.content
    assert len(context.result.hits) == 2


def test_empty_results_keep_structured_result():
    context = make_service(CapturingVectorStore([])).build_context("深蹲")

    assert context.content == "未检索到相关参考资料。"
    assert context.result.hits == ()


def test_repository_failure_is_unavailable_not_empty_evidence():
    class UnavailableStore:
        @staticmethod
        def hybrid_search(query, *, limit, source_filter=()):
            raise ConnectionError("Qdrant unavailable")

    service = make_service(UnavailableStore())

    with pytest.raises(RuntimeError, match="知识库检索暂时不可用"):
        service.retrieve("深蹲")
    context = service.build_context("深蹲")
    assert context.result is None
    assert "知识库检索暂时不可用" in context.content


def test_readiness_delegates_to_store():
    assert make_service(CapturingVectorStore()).readiness() == {"status": "ready"}
