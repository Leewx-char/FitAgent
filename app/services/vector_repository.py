"""基于 Qdrant 的向量仓储，对应用层仅暴露精简契约。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from langchain_core.documents import Document
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.utils.logger_handler import logger


@dataclass(frozen=True)
class IndexedChunk:
    """由离线知识索引构建器写入的规范化文本切片。"""

    chunk_id: str
    text: str
    metadata: dict[str, str | int | float | bool]


@dataclass(frozen=True)
class ScoredChunk:
    """检索到的文本切片及其 Qdrant 相似度分数。"""

    document: Document
    score: float


class VectorRepository(Protocol):
    """RAG 服务使用的存储边界；第三方 SDK 类型不得越过此处。"""

    def health(self) -> dict[str, int | str]:
        """返回向量仓储的就绪状态摘要。"""
        ...

    def search(
        self, query_vector: list[float], limit: int, source_filter: list[str] | None = None
    ) -> list[ScoredChunk]:
        """按向量查询并可限制结果来源。"""
        ...


class QdrantVectorRepository:
    """面向演示项目的、支持版本化 Qdrant 集合的读写实现。"""

    _TEXT_KEY = "text"
    _MAX_COLLECTION_EXISTS_ATTEMPTS = 3

    def __init__(
        self,
        collection_name: str,
        url: str,
        api_key: str | None = None,
        grpc_port: int = 6334,
        prefer_grpc: bool = True,
        timeout_seconds: int = 60,
        client: QdrantClient | None = None,
    ) -> None:
        """保存连接参数，并创建或接收 Qdrant 客户端。"""
        self.collection_name = collection_name
        self._url = url
        self._api_key = api_key
        self._grpc_port = grpc_port
        self._prefer_grpc = prefer_grpc
        self._timeout_seconds = timeout_seconds
        self._uses_injected_client = client is not None
        self.client = client or self._new_client()

    def _new_client(self) -> QdrantClient:
        """创建不做版本探测的 Qdrant 客户端，避免空闲连接影响离线构建。"""
        return QdrantClient(
            url=self._url,
            api_key=self._api_key,
            timeout=self._timeout_seconds,
            grpc_port=self._grpc_port,
            prefer_grpc=self._prefer_grpc,
            check_compatibility=False,
        )

    def _refresh_client_after_gateway_error(self) -> None:
        """丢弃可能被本地 Docker 代理关闭的 HTTP 会话。"""
        if not self._uses_injected_client:
            self.client = self._new_client()

    def health(self) -> dict[str, int | str]:
        """返回轻量就绪状态，不触发任何索引重建。"""
        collection = self.client.get_collection(self.collection_name)
        return {
            "status": "ready",
            "collection": self.collection_name,
            "points_count": int(collection.points_count or 0),
        }

    def _collection_exists_with_retry(self, collection_name: str) -> bool:
        """在 Docker 本地代理短暂返回 5xx 时重试只读的集合存在性查询。"""
        for attempt in range(self._MAX_COLLECTION_EXISTS_ATTEMPTS):
            try:
                return self.client.collection_exists(collection_name)
            except UnexpectedResponse as error:
                retriable = error.status_code in {502, 503, 504}
                if not retriable or attempt == self._MAX_COLLECTION_EXISTS_ATTEMPTS - 1:
                    raise
                logger.warning(
                    "Qdrant 集合查询遇到网关错误（%s），将创建新连接后重试：第 %s/%s 次",
                    error.status_code,
                    attempt + 1,
                    self._MAX_COLLECTION_EXISTS_ATTEMPTS,
                )
                self._refresh_client_after_gateway_error()
                time.sleep(attempt + 1)
        raise RuntimeError("集合存在性查询重试流程异常结束。")

    def create_collection(self, collection_name: str, vector_size: int) -> None:
        """创建一个索引版本集合及其唯一必需的负载索引。"""
        if self._collection_exists_with_retry(collection_name):
            raise RuntimeError(
                f"索引 revision collection 已存在：{collection_name}；"
                "请修改来源或切分配置以生成新的 revision。"
            )

        try:
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=vector_size, distance=models.Distance.COSINE
                ),
            )
            self.client.create_payload_index(
                collection_name=collection_name,
                field_name="source_id",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        except Exception:
            self._cleanup_ambiguous_collection_creation(collection_name)
            raise

    def _cleanup_ambiguous_collection_creation(self, collection_name: str) -> None:
        """清理服务端已创建、但客户端在创建响应前超时的未发布集合。"""
        try:
            if self.client.collection_exists(collection_name):
                self.client.delete_collection(collection_name=collection_name)
                logger.warning("已清理创建过程失败的未发布集合：%s", collection_name)
        except Exception:
            logger.exception("未能确认或清理创建失败的集合：%s", collection_name)

    def delete_collection(self, collection_name: str) -> None:
        """删除尚未发布、但在索引构建失败时遗留的集合版本。"""
        self.client.delete_collection(collection_name=collection_name)

    def upsert(
        self, collection_name: str, chunks: list[IndexedChunk], vectors: list[list[float]]
    ) -> None:
        """写入一批向量；文本切片元数据只能包含标量负载值。"""
        points = [
            models.PointStruct(
                id=chunk.chunk_id,
                vector=vector,
                payload={self._TEXT_KEY: chunk.text, **chunk.metadata},
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self.client.upsert(collection_name=collection_name, points=points, wait=True)

    def activate_alias(self, alias_name: str, collection_name: str) -> None:
        """以原子方式将应用别名切换至已完成校验的索引版本。"""
        operations: list[models.AliasOperations] = []
        aliases = self.client.get_aliases().aliases
        if any(alias.alias_name == alias_name for alias in aliases):
            operations.append(
                models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=alias_name))
            )
        operations.append(
            models.CreateAliasOperation(
                create_alias=models.CreateAlias(
                    collection_name=collection_name, alias_name=alias_name
                )
            )
        )
        self.client.update_collection_aliases(change_aliases_operations=operations)

    def search(
        self, query_vector: list[float], limit: int, source_filter: list[str] | None = None
    ) -> list[ScoredChunk]:
        """检索活动集合，并在打分前按来源过滤。"""
        query_filter = None
        if source_filter:
            query_filter = models.Filter(
                must=[
                    models.FieldCondition(key="source_id", match=models.MatchAny(any=source_filter))
                ]
            )

        response = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        results = []
        for point in response.points:
            payload = dict(point.payload or {})
            child_text = str(payload.pop(self._TEXT_KEY, ""))
            parent_text = str(payload.pop("parent_text", ""))
            if not child_text:
                continue
            payload["child_text"] = child_text
            document = Document(page_content=parent_text or child_text, metadata=payload)
            results.append(ScoredChunk(document, float(point.score)))
        return results

    def count(self) -> int:
        """返回活动集合的数量，供健康检查和校验使用。"""
        return int(self.client.count(collection_name=self.collection_name, exact=True).count)

    def active_revision(self) -> str | None:
        """读取一个负载值，确保混合检索的两路结果不会混用不同索引版本。"""
        points, _ = self.client.scroll(
            collection_name=self.collection_name,
            limit=1,
            with_payload=["index_revision"],
            with_vectors=False,
        )
        if not points:
            return None
        revision = (points[0].payload or {}).get("index_revision")
        return str(revision) if revision else None
