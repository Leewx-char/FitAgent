"""Qdrant 相似度检索的应用服务。

所有写入操作都在 ``knowledge_indexer`` 中完成。请求链路仅嵌入查询，并读取当前
活动的 Qdrant 集合。
"""

from __future__ import annotations

from app.core.settings import get_settings
from app.services.factory import get_embedding_model
from app.services.vector_repository import QdrantVectorRepository, ScoredChunk
from app.utils.config_handler import get_vector_store_config


class VectorStoreService:
    """在线 RAG 使用的只读向量检索服务。"""

    def __init__(self, repository: QdrantVectorRepository | None = None) -> None:
        """按配置创建仓储，或使用注入的 Qdrant 仓储。"""
        config = get_vector_store_config()
        settings = get_settings()
        self.repository = repository or QdrantVectorRepository(
            collection_name=config["collection_alias"],
            url=settings.qdrant_url or config["url"],
            api_key=settings.qdrant_api_key or None,
            grpc_port=config["grpc_port"],
            prefer_grpc=config["prefer_grpc"],
            timeout_seconds=config["qdrant_timeout_seconds"],
        )

    def similarity_search(
        self, query: str, limit: int, source_filter: list[str] | None = None
    ) -> list[ScoredChunk]:
        """嵌入查询文本，并从活动版本中取回带分数的切片。"""
        query_vector = get_embedding_model().embed_query(query)
        return self.repository.search(query_vector, limit, source_filter)

    def health(self) -> dict[str, int | str]:
        """暴露 Qdrant 就绪状态，不修改索引状态。"""
        return self.repository.health()

    def active_revision(self) -> str | None:
        """暴露活动版本，供 BM25 兼容性校验使用。"""
        return self.repository.active_revision()
