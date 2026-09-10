"""单次 Qdrant native hybrid 检索与证据上下文服务。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from app.core.request_context import request_id_var
from app.services.context_builder import ContextBuilder
from app.services.factory import get_reranker
from app.services.reranker import Reranker
from app.services.retrieval_contracts import RetrievalHit, RetrievalRequest, RetrievalResult
from app.services.vector_repository import ScoredChunk
from app.services.vector_store import VectorStoreService
from app.utils.config_handler import get_vector_store_config
from app.utils.logger_handler import logger


@dataclass(frozen=True)
class RagContext:
    """供 Agent 使用的文本上下文及其可展示证据。"""

    content: str
    result: RetrievalResult | None


class RagSummarizeService:
    """执行 Qdrant 首阶段召回、DashScope 排序并构建受预算约束的上下文。"""

    def __init__(
        self,
        vector_store: VectorStoreService | None = None,
        context_builder: ContextBuilder | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        """组装首阶段召回、二阶段排序与上下文组件，不加载本地索引工件。"""
        config = get_vector_store_config()
        self.vector_store = vector_store or VectorStoreService()
        self.top_k = config["k"]
        self.rerank_candidate_k = config["rerank_candidate_k"]
        if self.rerank_candidate_k < self.top_k:
            raise ValueError("rerank_candidate_k 不能小于最终 k。")
        self.reranker = reranker or get_reranker()
        self.context_builder = context_builder or ContextBuilder(
            max_context_chars=config["max_context_chars"],
            max_chars_per_evidence=config["max_chars_per_evidence"],
        )

    def readiness(self) -> dict[str, int | str]:
        """返回 Qdrant 就绪状态，不修改索引。"""
        return self.vector_store.health()

    @staticmethod
    def _to_hit(chunk: ScoredChunk, rank: int) -> RetrievalHit:
        """保留 Qdrant 的正文和最终分数，按返回顺序赋予排名。"""
        metadata = {
            str(key): value
            for key, value in chunk.document.metadata.items()
            if isinstance(value, (str, int, float, bool))
        }
        source_id = str(metadata["source_id"])
        chunk_id = str(metadata["chunk_id"])
        return RetrievalHit(
            evidence_id=f"{source_id}#{chunk_id}",
            source_id=source_id,
            chunk_id=chunk_id,
            text=chunk.document.page_content,
            rank=rank,
            score=chunk.score,
            metadata=metadata,
        )

    def retrieve(
        self,
        query: str,
        source_filter: list[str] | None = None,
    ) -> RetrievalResult:
        """原样执行一次首阶段查询与一次候选排序，仅记录最终证据和耗时。"""
        request = RetrievalRequest(
            query=query,
            source_filter=tuple(source_filter or ()),
            request_id=request_id_var.get(),
        )
        started_at = time.perf_counter()
        try:
            candidates = self.vector_store.hybrid_search(
                query, limit=self.rerank_candidate_k, source_filter=request.source_filter
            )
            chunks = self.reranker.rerank(query, candidates, limit=self.top_k)
        except Exception as error:
            logger.error("RAG 二阶段检索失败：%s", error, exc_info=True)
            raise RuntimeError("知识库检索暂时不可用，请稍后重试。") from error
        result = RetrievalResult(
            request=request,
            hits=tuple(self._to_hit(chunk, rank) for rank, chunk in enumerate(chunks, 1)),
            elapsed_ms=round((time.perf_counter() - started_at) * 1000),
        )
        logger.info("RAG_RETRIEVAL %s", json.dumps(result.log_payload(), ensure_ascii=False))
        return result

    @staticmethod
    def _format_references(hits: tuple[RetrievalHit, ...]) -> str:
        """给 Agent 提供可引用的稳定证据目录。"""
        entries = [f"[证据:{hit.rank}] {hit.evidence_id} | 来源={hit.source_id}" for hit in hits]
        return "\n证据目录：\n" + "\n".join(entries) if entries else ""

    def build_context(
        self,
        query: str,
        source_filter: list[str] | None = None,
    ) -> RagContext:
        """构建预算内上下文，并保留结构化证据供 API 层展示。"""
        try:
            result = self.retrieve(query, source_filter)
        except RuntimeError as error:
            return RagContext(str(error), None)
        if not result.hits:
            return RagContext("未检索到相关参考资料。", result)

        snippets = {
            snippet.evidence_id: snippet for snippet in self.context_builder.build(result.hits)
        }
        context_parts = []
        for hit in result.hits:
            snippet = snippets.get(hit.evidence_id)
            if snippet is None:
                continue
            location = hit.metadata.get("ordinal")
            location_text = f"来源={hit.source_id} | 证据ID={hit.evidence_id}"
            if location is not None:
                location_text += f" | 切片={location}"
            truncation = " | 已按上下文预算截取" if snippet.truncated else ""
            context_parts.append(
                f"[证据:{hit.rank}] {location_text}{truncation}\n{snippet.text.strip()}"
            )
        return RagContext(
            "\n\n".join(context_parts)
            + self._format_references(result.hits)
            + "\n回答若采用以上资料，请在相应结论后保留 [证据:N] 标记。",
            result,
        )
