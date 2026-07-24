"""基于只读活动 Qdrant 索引版本的在线 RAG 检索服务。"""

from __future__ import annotations

import re

from app.services.bm25_retriever import BM25Retriever
from app.services.vector_store import VectorStoreService
from app.utils.config_handler import get_synonyms_config, get_vector_store_config
from app.utils.logger_handler import logger
from app.utils.path_tool import get_abs_path
from langchain_core.documents import Document


class RagSummarizeService:
    """融合 Qdrant 稠密检索与可选的离线构建 BM25 工件。"""

    def __init__(self, vector_store: VectorStoreService | None = None) -> None:
        config = get_vector_store_config()
        self.vector_store = vector_store or VectorStoreService()
        self.top_k = config["k"]
        self.candidate_k = config.get("candidate_k", max(self.top_k * 2, self.top_k))
        self.min_relevance_score = config.get("min_relevance_score", 0.0)
        self.synonym_map = get_synonyms_config().get("expand", {})
        self.normalize_map = get_synonyms_config().get("normalize", {})
        self.bm25 = BM25Retriever()
        self.bm25_revision = self.bm25.load_artifact(get_abs_path(config["bm25_artifact_path"]))

    def readiness(self) -> dict[str, int | str]:
        """返回 Qdrant 就绪状态；该方法绝不导入或重建文档。"""
        return self.vector_store.health()

    def _normalize_query(self, query: str) -> str:
        normalized = re.sub(r"\s+", " ", query.strip().lower())
        for source, target in self.normalize_map.items():
            normalized = normalized.replace(source, target)
        return normalized

    def _expand_query(self, query: str) -> str:
        normalized = self._normalize_query(query)
        expansions = [
            candidate
            for phrase, candidates in self.synonym_map.items()
            if phrase in normalized
            for candidate in candidates
        ]
        return f"{normalized} {' '.join(expansions)}" if expansions else normalized

    @staticmethod
    def _document_terms(content: str) -> set[str]:
        return set(re.findall(r"[一-鿿]{2,}|[a-z0-9]+", content.lower()))

    def _deduplicate_docs(self, scored_docs: list[tuple], threshold: float = 0.8) -> list[tuple]:
        kept: list[tuple] = []
        kept_terms: list[set[str]] = []
        for doc, score in scored_docs:
            terms = self._document_terms(doc.page_content)
            if any(
                (union := len(terms | previous)) and len(terms & previous) / union > threshold
                for previous in kept_terms
            ):
                continue
            kept.append((doc, score))
            kept_terms.append(terms)
        return kept

    @staticmethod
    def _rrf_fusion(
        vector_results: list[tuple], bm25_results: list[tuple], k: int = 60
    ) -> list[tuple]:
        """按稳定的切片 ID 融合已独立过滤的两路检索结果。"""
        ranks: dict[tuple[str, str], float] = {}
        documents: dict[tuple[str, str], Document] = {}
        for results in (vector_results, bm25_results):
            for rank, (document, _) in enumerate(results, start=1):
                key = (
                    str(document.metadata.get("source_id", document.metadata.get("source", ""))),
                    str(document.metadata.get("chunk_id", document.metadata.get("ordinal", ""))),
                )
                ranks[key] = ranks.get(key, 0.0) + 1.0 / (k + rank)
                documents[key] = document
        scored = ((documents[key], score) for key, score in ranks.items())
        return sorted(scored, key=lambda item: item[1], reverse=True)

    def retriever_docs(self, query: str, source_filter: list[str] | None = None) -> list[Document]:
        """仅从已就绪的 Qdrant 集合检索；失败时不改变任何状态。"""
        expanded_query = self._expand_query(query)
        try:
            vector_candidates = self.vector_store.similarity_search(
                expanded_query, self.candidate_k, source_filter
            )
        except Exception as error:
            logger.error(f"Qdrant 向量检索失败：{error}", exc_info=True)
            raise RuntimeError("知识库检索暂时不可用，请稍后重试。") from error

        vector_results = [
            (item.document, item.score)
            for item in vector_candidates
            if item.score >= self.min_relevance_score
        ]
        active_revision = self.vector_store.active_revision()
        if self.bm25_revision and self.bm25_revision == active_revision:
            bm25_results = self.bm25.search(
                expanded_query, k=self.candidate_k, source_filter=source_filter
            )
        else:
            bm25_results = []
            if self.bm25_revision:
                logger.warning(
                    "BM25 工件 revision 与 Qdrant 不一致，降级为 dense 检索：%s != %s",
                    self.bm25_revision,
                    active_revision,
                )
        scored_docs = self._deduplicate_docs(self._rrf_fusion(vector_results, bm25_results))
        documents = [document for document, _ in scored_docs[: self.top_k]]
        logger.info(
            "RAG检索完成：query=%s vector=%s bm25=%s selected=%s revision=%s",
            query,
            len(vector_results),
            len(bm25_results),
            len(documents),
            self.bm25_revision or "dense-only",
        )
        return documents

    @staticmethod
    def _format_references(documents: list[Document]) -> str:
        references = []
        seen = set()
        for document in documents:
            source = document.metadata.get("source_id", document.metadata.get("source", "未知来源"))
            page = document.metadata.get("page")
            reference = f"{source} 第{page + 1}页" if isinstance(page, int) else str(source)
            if reference not in seen:
                seen.add(reference)
                references.append(reference)
        return "\n参考来源：\n- " + "\n- ".join(references) if references else ""

    def rag_summarize(self, query: str, source_filter: list[str] | None = None) -> str:
        try:
            documents = self.retriever_docs(query, source_filter)
        except RuntimeError as error:
            return str(error)
        if not documents:
            return "未检索到相关参考资料。"

        context_parts = []
        for counter, document in enumerate(documents, start=1):
            source = document.metadata.get("source_id", document.metadata.get("source", "未知来源"))
            location = document.metadata.get("ordinal", document.metadata.get("chunk_index"))
            location_text = f"来源={source}"
            if location is not None:
                location_text += f" | 切片={location}"
            context_parts.append(
                f"[参考资料{counter}] {location_text}\n{document.page_content.strip()}"
            )
        return "\n\n".join(context_parts) + self._format_references(documents)
