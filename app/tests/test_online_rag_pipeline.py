"""单次 embedding 和 native hybrid 转调、上下文预算测试。"""

from types import SimpleNamespace

import pytest

from app.services import vector_store
from app.services.context_builder import ContextBuilder
from app.services.retrieval_contracts import RetrievalHit


def test_vector_store_embeds_once_and_delegates_native_hybrid(monkeypatch):
    embedding_calls = []
    repository_calls = []
    hits = [object()]

    def embed_query(query):
        embedding_calls.append(query)
        return [0.1, 0.2]

    def hybrid_search(query, dense_vector, **kwargs):
        repository_calls.append((query, dense_vector, kwargs))
        return hits

    monkeypatch.setattr(
        vector_store, "get_embedding_model", lambda: SimpleNamespace(embed_query=embed_query)
    )
    service = vector_store.VectorStoreService(
        repository=SimpleNamespace(hybrid_search=hybrid_search)
    )

    result = service.hybrid_search("  深蹲？ ", limit=4, source_filter=("动作.md",))

    assert embedding_calls == ["  深蹲？ "]
    assert repository_calls == [
        (
            "  深蹲？ ",
            [0.1, 0.2],
            {
                "limit": 4,
                "candidate_limit": 15,
                "source_filter": ("动作.md",),
            },
        )
    ]
    assert result is hits


def test_default_vector_store_uses_canonical_collection_without_querying(monkeypatch):
    captured = {}

    def repository_factory(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(vector_store, "QdrantVectorRepository", repository_factory)
    vector_store.VectorStoreService()

    assert captured["collection_name"] == "fitagent_knowledge"


def make_hit(text, rank=1):
    """构造只有最终检索字段的证据。"""
    return RetrievalHit(
        evidence_id=f"动作.md#chunk-{rank}",
        source_id="动作.md",
        chunk_id=f"chunk-{rank}",
        text=text,
        rank=rank,
        score=0.9,
        metadata={},
    )


def test_context_builder_clips_chunk_text_inside_total_budget():
    hits = (make_hit("深蹲时膝盖追踪脚尖方向。" * 20), make_hit("热身" * 100, 2))

    snippets = ContextBuilder(max_context_chars=50, max_chars_per_evidence=30).build(hits)

    assert [snippet.evidence_id for snippet in snippets] == [hit.evidence_id for hit in hits]
    assert [len(snippet.text) for snippet in snippets] == [30, 20]
    assert all(snippet.truncated for snippet in snippets)
    assert snippets[0].text.startswith("深蹲时膝盖追踪脚尖方向。")


@pytest.mark.parametrize("budget", [0, 1, 2, 3, 4])
def test_context_builder_enforces_tiny_budgets(budget):
    snippets = ContextBuilder(max_context_chars=budget, max_chars_per_evidence=budget).build(
        (make_hit("深蹲时膝盖追踪脚尖方向。"),)
    )

    assert sum(len(snippet.text) for snippet in snippets) <= budget
    assert not snippets if budget == 0 else snippets[0].truncated


def test_context_builder_preserves_complete_short_text():
    snippet = ContextBuilder().build((make_hit("深蹲资料"),))[0]

    assert snippet.text == "深蹲资料"
    assert snippet.truncated is False
