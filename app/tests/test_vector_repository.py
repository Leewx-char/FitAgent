import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from httpx import Headers

from app.services import vector_repository
from app.services.vector_repository import IndexedChunk, QdrantVectorRepository


def test_qdrant_repository_skips_eager_version_probe(monkeypatch):
    """验证仓储初始化禁用 Qdrant 的即时版本兼容性探测。"""
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            """捕获仓储初始化传给 Qdrant 客户端的选项。"""
            captured.update(kwargs)

    monkeypatch.setattr(vector_repository, "QdrantClient", FakeClient)

    QdrantVectorRepository("rag_active", "http://qdrant", api_key="test-key")

    assert captured["check_compatibility"] is False
    assert captured["prefer_grpc"] is True
    assert captured["grpc_port"] == 6334
    assert captured["timeout"] == 60


def test_qdrant_repository_retries_transient_collection_gateway_error(monkeypatch):
    """验证集合存在性检查会重试短暂的 502 网关错误。"""
    class TransientClient:
        def __init__(self):
            """初始化集合存在性检查次数。"""
            self.calls = 0

        def collection_exists(self, _collection_name):
            """首次模拟 502，后续返回集合不存在。"""
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
    """验证网关错误后仓储刷新网络客户端再执行检查。"""
    created_clients = []

    class TransientClient:
        def __init__(self, **_kwargs):
            """首个客户端标记为失败，后续客户端可正常响应。"""
            self.should_fail = len(created_clients) == 0
            created_clients.append(self)

        def collection_exists(self, _collection_name):
            """失败客户端抛 502，替换后的客户端返回不存在。"""
            if self.should_fail:
                raise UnexpectedResponse(502, "Bad Gateway", b"", Headers())
            return False

    monkeypatch.setattr(vector_repository, "QdrantClient", TransientClient)
    monkeypatch.setattr(vector_repository.time, "sleep", lambda _seconds: None)
    repository = QdrantVectorRepository("rag_active", "http://qdrant", api_key="test-key")

    assert repository._collection_exists_with_retry("rag_next") is False
    assert len(created_clients) == 2


def test_qdrant_repository_cleans_collection_when_create_response_times_out():
    """验证创建集合超时且状态不明时清理可能残留的集合。"""
    class AmbiguousCreateClient:
        def __init__(self):
            """初始化创建后集合存在性检查和删除记录。"""
            self.exists_calls = 0
            self.deleted: list[str] = []

        def collection_exists(self, _collection_name):
            """第一次视为不存在，后续视为可能已创建。"""
            self.exists_calls += 1
            return self.exists_calls > 1

        def create_collection(self, **_kwargs):
            """模拟创建集合的请求超时。"""
            raise RuntimeError("Deadline Exceeded")

        def delete_collection(self, *, collection_name):
            """记录清理状态不明集合的删除请求。"""
            self.deleted.append(collection_name)

    client = AmbiguousCreateClient()
    repository = QdrantVectorRepository("rag_active", "http://unused", client=client)

    with pytest.raises(RuntimeError, match="Deadline Exceeded"):
        repository.create_collection("rag_incomplete", vector_size=2)

    assert client.deleted == ["rag_incomplete"]


def test_qdrant_repository_filters_by_source_and_activates_revision():
    """验证向量仓储激活版本别名后能按来源筛选检索结果。"""
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
