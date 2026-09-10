from collections.abc import Mapping
from types import SimpleNamespace

import pytest
from qdrant_client import models
from qdrant_client.conversions.conversion import RestToGrpc

from app.services.vector_repository import IndexedChunk, QdrantVectorRepository


class CapturingClient:
    """捕获仓储发往 Qdrant 的请求，而不依赖本地推理能力。"""

    def __init__(self, points=None, collection=None):
        self.calls = []
        self.recreate_collection_kwargs = None
        self.payload_index_kwargs = None
        self.upsert_kwargs = None
        self.query_points_calls = 0
        self.kwargs = None
        self._points = points or []
        self._collection = collection

    def recreate_collection(self, **kwargs):
        self.calls.append(("recreate_collection", kwargs))
        self.recreate_collection_kwargs = kwargs

    def create_payload_index(self, **kwargs):
        self.calls.append(("create_payload_index", kwargs))
        self.payload_index_kwargs = kwargs

    def upsert(self, **kwargs):
        self.calls.append(("upsert", kwargs))
        self.upsert_kwargs = kwargs

    def query_points(self, **kwargs):
        self.calls.append(("query_points", kwargs))
        self.query_points_calls += 1
        self.kwargs = kwargs
        return SimpleNamespace(points=self._points)

    def get_collection(self, collection_name):
        self.calls.append(("get_collection", collection_name))
        return self._collection


def test_health_reads_collection_and_returns_ready_summary():
    client = CapturingClient(collection=SimpleNamespace(points_count=17))
    repository = QdrantVectorRepository("fitagent_knowledge", "http://unused", client=client)

    health = repository.health()

    assert health == {
        "status": "ready",
        "collection": "fitagent_knowledge",
        "points_count": 17,
    }
    assert client.calls == [("get_collection", "fitagent_knowledge")]


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
    assert point.vector["sparse"].options["tokenizer"] == "multilingual"
    assert point.vector["sparse"].options["language"] == "chinese"
    assert point.vector["sparse"].options["avg_len"] == 400
    assert point.payload == {"text": chunk.text, **chunk.metadata}


def test_bm25_documents_use_grpc_serializable_options_for_indexing_and_search():
    client = CapturingClient()
    repository = QdrantVectorRepository("fitagent_knowledge", "http://unused", client=client)
    chunk = IndexedChunk(
        "5e196284-177a-5ee8-b496-a8582a50f9d1",
        "深蹲时保持膝盖与脚尖方向一致。",
        {"source_id": "动作.md", "ordinal": 0},
    )

    repository.rebuild([chunk], [[0.1, 0.2]])

    indexed_point = client.upsert_kwargs["points"][0]
    indexed_sparse_document = indexed_point.vector["sparse"]
    RestToGrpc.convert_point_struct(indexed_point)
    assert isinstance(indexed_sparse_document.options, Mapping)
    assert dict(indexed_sparse_document.options) == {
        "k": 1.2,
        "b": 0.75,
        "avg_len": 400.0,
        "tokenizer": "multilingual",
        "language": "chinese",
    }

    repository.hybrid_search("深蹲膝盖内扣", [0.1, 0.2], limit=6, candidate_limit=15)

    sparse_prefetch = client.kwargs["prefetch"][1]
    sparse_query_document = sparse_prefetch.query
    RestToGrpc.convert_prefetch_query(sparse_prefetch)
    assert isinstance(sparse_query_document.options, Mapping)
    assert dict(sparse_query_document.options) == dict(indexed_sparse_document.options)


def test_rebuild_rejects_mismatched_inputs_before_recreating_collection():
    client = CapturingClient()
    repository = QdrantVectorRepository("fitagent_knowledge", "http://unused", client=client)
    chunk = IndexedChunk(
        "5e196284-177a-5ee8-b496-a8582a50f9d1",
        "深蹲时保持膝盖与脚尖方向一致。",
        {"source_id": "动作.md"},
    )

    with pytest.raises(ValueError, match="数量必须一致"):
        repository.rebuild([chunk], [[0.1, 0.2], [0.3, 0.4]])

    assert client.recreate_collection_kwargs is None


def test_rebuild_preserves_chunk_text_when_metadata_contains_text():
    client = CapturingClient()
    repository = QdrantVectorRepository("fitagent_knowledge", "http://unused", client=client)
    chunk = IndexedChunk(
        "5e196284-177a-5ee8-b496-a8582a50f9d1",
        "深蹲时保持膝盖与脚尖方向一致。",
        {"source_id": "动作.md", "text": "错误元数据文本"},
    )

    repository.rebuild([chunk], [[0.1, 0.2]])

    assert client.upsert_kwargs["points"][0].payload["text"] == chunk.text


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
