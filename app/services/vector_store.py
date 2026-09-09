"""Qdrant native hybrid 检索的应用服务。

所有写入操作都在 ``knowledge_indexer`` 中完成。请求链路仅嵌入查询，并读取当前
Qdrant 知识集合。
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
        self.candidate_limit = config["candidate_k"]
        self.repository = repository or QdrantVectorRepository(
            collection_name=config["collection_name"],
            url=settings.qdrant_url or config["url"],
            api_key=settings.qdrant_api_key or None,
            grpc_port=config["grpc_port"],
            prefer_grpc=config["prefer_grpc"],
            timeout_seconds=config["qdrant_timeout_seconds"],
        )

    def hybrid_search(
        self, query: str, *, limit: int, source_filter: tuple[str, ...] = ()
    ) -> list[ScoredChunk]:
        """嵌入原始查询一次，并转调 Qdrant 的单次混合检索。"""
        dense_vector = get_embedding_model().embed_query(query)
        return self.repository.hybrid_search(
            query,
            dense_vector,
            limit=limit,
            candidate_limit=self.candidate_limit,
            source_filter=source_filter,
        )

    def health(self) -> dict[str, int | str]:
        """暴露 Qdrant 就绪状态，不修改索引状态。"""
        return self.repository.health()
