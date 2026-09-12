"""Qdrant native hybrid 检索的仓储边界。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from langchain_core.documents import Document
from qdrant_client import QdrantClient, models

from app.utils.config_handler import get_vector_store_config


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


def _source_filter(source_filter: Sequence[str] | None) -> models.Filter | None:
    """将可选来源列表转换为两个 prefetch 共用的 Qdrant 过滤条件。"""
    if not source_filter:
        return None
    return models.Filter(
        must=[
            models.FieldCondition(key="source_id", match=models.MatchAny(any=list(source_filter)))
        ]
    )


class QdrantVectorRepository:
    """单 collection 的 Qdrant native dense + BM25 hybrid 仓储。"""

    _TEXT_KEY = "text"

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
        """保存连接参数和可评估的 BM25 文档平均长度。"""
        self.collection_name = collection_name
        self.bm25_avg_len = int(get_vector_store_config()["bm25_avg_len"])
        self.client = client or QdrantClient(
            url=url,
            api_key=api_key,
            timeout=timeout_seconds,
            grpc_port=grpc_port,
            prefer_grpc=prefer_grpc,
            check_compatibility=False,
        )

    def _bm25_config(self) -> models.Bm25Config:
        """返回索引和查询一致使用的中文 BM25 配置。"""
        return models.Bm25Config(
            tokenizer=models.TokenizerType.MULTILINGUAL,
            language="chinese",
            avg_len=self.bm25_avg_len,
        )

    def _bm25_options(self) -> dict[str, object]:
        """将 BM25 配置序列化为 Qdrant gRPC 可接受的 JSON 映射。"""
        return self._bm25_config().model_dump(mode="json", exclude_none=True)

    def health(self) -> dict[str, int | str]:
        """返回 collection 的只读就绪状态，不触发索引变更。"""
        collection = self.client.get_collection(self.collection_name)
        return {
            "status": "ready",
            "collection": self.collection_name,
            "points_count": int(collection.points_count or 0),
        }

    def rebuild(self, chunks: list[IndexedChunk], dense_vectors: list[list[float]]) -> None:
        """破坏性地重建 collection，并批量写入 dense 与 BM25 向量。"""
        if len(chunks) != len(dense_vectors):
            raise ValueError("Qdrant 重建的 chunks 与 dense vectors 数量必须一致。")
        if not dense_vectors:
            raise ValueError("重建 Qdrant collection 至少需要一个 dense vector。")

        self.client.recreate_collection(
            collection_name=self.collection_name,
            vectors_config={
                "dense": models.VectorParams(
                    size=len(dense_vectors[0]), distance=models.Distance.COSINE
                ),
            },
            sparse_vectors_config={
                "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF),
            },
        )
        self.client.create_payload_index(
            collection_name=self.collection_name,
            field_name="source_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        points = [
            models.PointStruct(
                id=chunk.chunk_id,
                vector={
                    "dense": dense_vector,
                    "sparse": models.Document(
                        text=chunk.text,
                        model="Qdrant/bm25",
                        options=self._bm25_options(),
                    ),
                },
                payload={**chunk.metadata, self._TEXT_KEY: chunk.text},
            )
            for chunk, dense_vector in zip(chunks, dense_vectors, strict=True)
        ]
        self.client.upsert(collection_name=self.collection_name, points=points, wait=True)

    def hybrid_search(
        self,
        query: str,
        dense_vector: list[float],
        *,
        limit: int,
        candidate_limit: int,
        source_filter: Sequence[str] | None = None,
    ) -> list[ScoredChunk]:
        """在一次 Qdrant Query API 请求中执行 dense、BM25 与 RRF 融合。"""
        query_filter = _source_filter(source_filter)
        response = self.client.query_points(
            collection_name=self.collection_name,
            prefetch=[
                models.Prefetch(
                    query=dense_vector,
                    using="dense",
                    filter=query_filter,
                    limit=candidate_limit,
                ),
                models.Prefetch(
                    query=models.Document(
                        text=query,
                        model="Qdrant/bm25",
                        options=self._bm25_options(),
                    ),
                    using="sparse",
                    filter=query_filter,
                    limit=candidate_limit,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [
            ScoredChunk(
                document=Document(
                    page_content=str(
                        (payload := dict(point.payload or {})).pop(self._TEXT_KEY, "")
                    ),
                    metadata=payload,
                ),
                score=float(point.score),
            )
            for point in response.points
        ]
