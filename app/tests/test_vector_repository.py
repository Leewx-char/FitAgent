from types import SimpleNamespace

from qdrant_client import models

from app.services.vector_repository import IndexedChunk, QdrantVectorRepository


class CapturingClient:
    """捕获仓储发往 Qdrant 的请求，而不依赖本地推理能力。"""

    def __init__(self, points=None):
        self.recreate_collection_kwargs = None
        self.payload_index_kwargs = None
        self.upsert_kwargs = None
        self.query_points_calls = 0
        self.kwargs = None
        self._points = points or []

    def recreate_collection(self, **kwargs):
        self.recreate_collection_kwargs = kwargs

    def create_payload_index(self, **kwargs):
        self.payload_index_kwargs = kwargs

    def upsert(self, **kwargs):
        self.upsert_kwargs = kwargs

    def query_points(self, **kwargs):
        self.query_points_calls += 1
        self.kwargs = kwargs
        return SimpleNamespace(points=self._points)


def test_rebuild_creates_named_dense_and_sparse_vectors_and_upserts_both():
    client = CapturingClient()
    repository = QdrantVectorRepository("fitagent_knowledge", "http://unused", client=client)
    chunk = IndexedChunk(
        "5e196284-177a-5ee8-b496-a8582a50f9d1",
        "深蹲时保持膝盖与脚尖方向一致。",
        {"source_id": "动作.md", "ordinal": 0},
    )

    repository.rebuild([chunk], [[0.1, 0.2]])

    schema = client.recreate_collection_kwargs
    assert schema["collection_name"] == "fitagent_knowledge"
    assert schema["vectors_config"]["dense"].size == 2
    assert schema["vectors_config"]["dense"].distance == models.Distance.COSINE
    assert schema["sparse_vectors_config"]["sparse"].modifier == models.Modifier.IDF
    assert client.payload_index_kwargs == {
        "collection_name": "fitagent_knowledge",
        "field_name": "source_id",
        "field_schema": models.PayloadSchemaType.KEYWORD,
    }
    point = client.upsert_kwargs["points"][0]
    assert point.vector["dense"] == [0.1, 0.2]
    assert point.vector["sparse"].text == chunk.text
    assert point.vector["sparse"].model == "Qdrant/bm25"
    assert point.vector["sparse"].options.tokenizer == models.TokenizerType.MULTILINGUAL
    assert point.vector["sparse"].options.language == "chinese"
    assert point.vector["sparse"].options.avg_len == 256
    assert point.payload == {"text": chunk.text, **chunk.metadata}


def test_hybrid_search_uses_two_prefetches_and_one_rrf_query():
    client = CapturingClient()
    repository = QdrantVectorRepository("fitagent_knowledge", "http://unused", client=client)

    repository.hybrid_search(
        "深蹲膝盖内扣",
        [0.1, 0.2],
        limit=6,
        candidate_limit=15,
        source_filter=("动作.md",),
    )

    assert client.query_points_calls == 1
    assert len(client.kwargs["prefetch"]) == 2
    assert client.kwargs["query"].fusion == models.Fusion.RRF
    assert all(prefetch.filter is not None for prefetch in client.kwargs["prefetch"])
    assert client.kwargs["prefetch"][0].using == "dense"
    assert client.kwargs["prefetch"][0].query == [0.1, 0.2]
    assert client.kwargs["prefetch"][1].using == "sparse"
    assert client.kwargs["prefetch"][1].query.model == "Qdrant/bm25"
    assert client.kwargs["limit"] == 6
    assert client.kwargs["with_payload"] is True
    assert client.kwargs["with_vectors"] is False


def test_hybrid_search_maps_qdrant_points_to_scored_chunks():
    client = CapturingClient(
        [
            SimpleNamespace(
                payload={"text": "深蹲时保持脊柱中立。", "source_id": "动作.md", "ordinal": 1},
                score=0.75,
            )
        ]
    )
    repository = QdrantVectorRepository("fitagent_knowledge", "http://unused", client=client)

    results = repository.hybrid_search("深蹲", [0.1, 0.2], limit=6, candidate_limit=15)

    assert len(results) == 1
    assert results[0].document.page_content == "深蹲时保持脊柱中立。"
    assert results[0].document.metadata == {"source_id": "动作.md", "ordinal": 1}
    assert results[0].score == 0.75
