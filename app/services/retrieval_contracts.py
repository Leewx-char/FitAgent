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
    text: str
    rank: int
    score: float
    metadata: dict[str, str | int | float | bool]


@dataclass(frozen=True)
class RetrievalResult:
    """一次检索的结果及其最小可观测指标。"""

    request: RetrievalRequest
    hits: tuple[RetrievalHit, ...]
    elapsed_ms: int

    def log_payload(self) -> dict[str, str | int | bool]:
        """返回可写入日志的非敏感摘要，不记录原始用户问题。"""

        return {
            "request_id": self.request.request_id,
            "query_length": len(self.request.query),
            "selected": len(self.hits),
            "elapsed_ms": self.elapsed_ms,
        }
