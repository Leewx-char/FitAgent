"""在线 RAG 检索层的稳定数据契约。

这些类型隔离了 LangChain ``Document`` 与 Qdrant 的实现细节。调用方只需关心查询、
证据、排序和本次检索的可观测信息，后续替换检索引擎时不需要改变 Agent 工具接口。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalRequest:
    """一次只读检索请求。"""

    query: str
    source_filter: tuple[str, ...] = ()
    request_id: str = ""


@dataclass(frozen=True)
class RetrievalHit:
    """可供回答引用的一条检索证据。"""

    evidence_id: str
    source_id: str
    chunk_id: str
    parent_id: str
    text: str
    child_text: str
    rank: int
    score: float
    dense_rank: int | None
    bm25_rank: int | None
    rerank_score: float | None
    metadata: dict[str, str | int | float | bool]
    metadata_tag_score: float | None = None
    source_quality_penalty: float = 0.0


@dataclass(frozen=True)
class RetrievalResult:
    """一次检索的结果及其最小可观测指标。"""

    request: RetrievalRequest
    expanded_query: str
    search_queries: tuple[str, ...]
    index_revision: str | None
    hits: tuple[RetrievalHit, ...]
    vector_candidate_count: int
    bm25_candidate_count: int
    elapsed_ms: int
    bm25_enabled: bool
    query_planner_used_llm: bool
    query_tags: tuple[str, ...] = ()
    query_planner_fallback_reason: str = ""

    def log_payload(self) -> dict[str, str | int | bool]:
        """返回可写入日志的非敏感摘要，不记录原始用户问题。"""

        return {
            "request_id": self.request.request_id,
            "query_length": len(self.request.query),
            "expanded_query_length": len(self.expanded_query),
            "search_query_count": len(self.search_queries),
            "index_revision": self.index_revision or "unknown",
            "vector_candidates": self.vector_candidate_count,
            "bm25_candidates": self.bm25_candidate_count,
            "selected": len(self.hits),
            "elapsed_ms": self.elapsed_ms,
            "bm25_enabled": self.bm25_enabled,
            "query_planner_used_llm": self.query_planner_used_llm,
            "query_planner_fallback": bool(self.query_planner_fallback_reason),
            "query_tag_count": len(self.query_tags),
        }
