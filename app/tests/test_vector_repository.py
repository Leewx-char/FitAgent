import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from httpx import Headers

from app.services import vector_repository
from app.services.vector_repository import IndexedChunk, QdrantVectorRepository


def test_qdrant_repository_skips_eager_version_probe(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(vector_repository, "QdrantClient", FakeClient)

    QdrantVectorRepository("rag_active", "http://qdrant", api_key="test-key")

    assert captured["check_compatibility"] is False
    assert captured["prefer_grpc"] is True
    assert captured["grpc_port"] == 6334
    assert captured["timeout"] == 60


def test_qdrant_repository_retries_transient_collection_gateway_error(monkeypatch):
    class TransientClient:
        def __init__(self):
            self.calls = 0

        def collection_exists(self, _collection_name):
            self.calls += 1
            if self.calls == 1:
                raise UnexpectedResponse(502, "Bad Gateway", b"", Headers())
            return False

    client = TransientClient()
    repository = QdrantVectorRepository("rag_active", "http://unused", client=client)
    monkeypatch.setattr(vector_repository.time, "sleep", lambda _seconds: None)

    assert repository._collection_exists_with_retry("rag_next") is False
    assert client.calls == 2


def test_qdrant_repository_refreshes_network_client_after_gateway_error(monkeypatch):
    created_clients = []

    class TransientClient:
        def __init__(self, **_kwargs):
            self.should_fail = len(created_clients) == 0
            created_clients.append(self)

        def collection_exists(self, _collection_name):
            if self.should_fail:
                raise UnexpectedResponse(502, "Bad Gateway", b"", Headers())
            return False

    monkeypatch.setattr(vector_repository, "QdrantClient", TransientClient)
    monkeypatch.setattr(vector_repository.time, "sleep", lambda _seconds: None)
    repository = QdrantVectorRepository("rag_active", "http://qdrant", api_key="test-key")

    assert repository._collection_exists_with_retry("rag_next") is False
    assert len(created_clients) == 2


def test_qdrant_repository_cleans_collection_when_create_response_times_out():
    class AmbiguousCreateClient:
        def __init__(self):
            self.exists_calls = 0
            self.deleted: list[str] = []

        def collection_exists(self, _collection_name):
            self.exists_calls += 1
            return self.exists_calls > 1

        def create_collection(self, **_kwargs):
            raise RuntimeError("Deadline Exceeded")

        def delete_collection(self, *, collection_name):
            self.deleted.append(collection_name)

    client = AmbiguousCreateClient()
    repository = QdrantVectorRepository("rag_active", "http://unused", client=client)

    with pytest.raises(RuntimeError, match="Deadline Exceeded"):
        repository.create_collection("rag_incomplete", vector_size=2)

    assert client.deleted == ["rag_incomplete"]


def test_qdrant_repository_filters_by_source_and_activates_revision():
    client = QdrantClient(":memory:")
    repository = QdrantVectorRepository("rag_v1", "http://unused", client=client)
    repository.create_collection("rag_v1", vector_size=2)
    repository.upsert(
        "rag_v1",
        [
            IndexedChunk(
                "5e196284-177a-5ee8-b496-a8582a50f9d1",
                "深蹲动作要保持脊柱中立。",
                {
                    "source_id": "动作指南大全.txt",
                    "ordinal": 0,
                    "index_revision": "revision-1",
                },
            ),
            IndexedChunk(
                "bc574e94-c5ee-5c58-8d68-27f9f72fa596",
                "蛋白质有助于训练后的恢复。",
                {
                    "source_id": "营养学知识.txt",
                    "ordinal": 0,
                    "index_revision": "revision-1",
                },
            ),
        ],
        [[1.0, 0.0], [0.0, 1.0]],
    )

    repository.activate_alias("rag_active", "rag_v1")
    results = repository.search([1.0, 0.0], limit=5, source_filter=["动作指南大全.txt"])

    assert len(results) == 1
    assert results[0].document.metadata["source_id"] == "动作指南大全.txt"
    assert "深蹲" in results[0].document.page_content
    assert repository.active_revision() == "revision-1"
