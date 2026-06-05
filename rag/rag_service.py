
"""
总结服务类：用户提问，搜索参考资料，讲提问和参考资料提交给模型，让模型总结回复
"""
import re, threading
from utils.config_handler import chroma_conf, synonyms_conf
from utils.logger_handler import logger
from rag.vector_store import VectorStoreService

class RagSummarizeService(object):
    def __init__(self):
        self.vector_store = VectorStoreService()
        self._collection_ready_checked = False
        self._repair_lock = threading.Lock()

        self.top_k = chroma_conf["k"]
        self.candidate_k = chroma_conf.get("candidate_k", max(self.top_k * 2, self.top_k))
        self.min_relevance_score = chroma_conf.get("min_relevance_score", 0.0)

        # ：查询扩展——当用户输入包含某个关键词时，自动把同义词也加入搜索词，提高知识库召回率。
        self.synonym_map = synonyms_conf.get("expand", {})  # 同义词扩展
        self.normalize_map = synonyms_conf.get("normalize", {})  # 归一化替换
        self.stopwords = synonyms_conf.get("stopwords", set())  # 停用词



    def _ensure_collection_ready(self):
        if self._collection_ready_checked:
            return
        self._collection_ready_checked = True

        try:
            current_count = self.vector_store.vector_store._collection.count()
        except Exception as e:
            logger.error(f"获取向量库文档数量失败：{str(e)}", exc_info=True)

        if current_count > 0:
            logger.info(f"当前向量库已有文档，数量：{current_count}")
            return

        logger.warning("检测到向量库为空，开始自动加载知识文档")
        try:
            self.vector_store.load_document()
            latest_count = self.vector_store.vector_store._collection.count()
            logger.info(f"自动加载完成，当前向量库文档数量：{latest_count}")
        except Exception as e:
            logger.error(f"自动加载知识文档失败：{str(e)}", exc_info=True)

    @staticmethod
    def _is_corrupted_index_error(error: Exception) -> bool:
        message = str(error).lower()
        return (
            "hnsw segment reader" in message
            or "nothing found on disk" in message
            or "error executing plan" in message
        )

    def _repair_vector_store(self):
        with self._repair_lock:
            logger.warning("检测到向量索引异常，开始重建向量库")
            self.vector_store.reset_store()
            self.vector_store.load_document()
            self._collection_ready_checked = True
            latest_count = self.vector_store.vector_store._collection.count()
            logger.info(f"向量库重建完成，当前文档数量：{latest_count}")

    # 查询替换——把用户的口语化/非标准用词统一替换成知识库里的标准术语，提高向量检索命中率。
    def _normalize_query(self, query: str) -> str:
        normalized = re.sub(r"\s+", " ", query.strip().lower())
        replacements = self.normalize_map
        for source, target in replacements.items():
            normalized = normalized.replace(source, target)
        return normalized

    def _expand_query(self, query: str) -> str:
        normalized = self._normalize_query(query)
        expansions = []
        for phrase, candidates in self.synonym_map.items():
            if phrase in normalized:
                expansions.extend(candidates)
        if expansions:
            normalized = f"{normalized} {' '.join(expansions)}"
        return normalized

    def _query_terms(self, query: str) -> set[str]:
        expanded = self._expand_query(query)
        terms = set()
        for term in re.findall(r"[一-鿿]{2,}|[a-z0-9]+", expanded):
            if term not in self.stopwords:
                terms.add(term)
        return terms

    @staticmethod
    def _document_terms(content: str) -> set[str]:
        return set(re.findall(r"[一-鿿]{2,}|[a-z0-9]+", content.lower()))

    def _rerank_score(self, query_terms: set[str], content: str, relevance_score: float) -> float:
        doc_terms = self._document_terms(content)
        overlap = len(query_terms & doc_terms)
        coverage = overlap / max(len(query_terms), 1)
        return relevance_score * 0.7 + coverage * 0.3

    # threshold = 0.8：只有当 80% 以上的词重叠时才视为重复
    def _deduplicate_docs(self, scored_docs: list[tuple], threshold: float = 0.8) -> list[tuple]:
        kept = []  # 最终保留的 (doc, score) 列表
        kept_terms = []  # 对应的词集合列表，用于和后续文档比较

        for doc, score in scored_docs:
            # scored_docs 已按 rerank_score 降序排列
            # 所以先处理的文档分数更高

            doc_terms = self._document_terms(doc.page_content)  # 提取当前文档的词集合
            is_dup = False

            for prev_terms in kept_terms:
                # 和每个已保留的文档比较
                # Jaccard = 交集大小 / 并集大小

                intersection = len(doc_terms & prev_terms)  # 交集大小
                union = len(doc_terms | prev_terms)  # 并集大小
                if union > 0 and intersection / union > threshold:
                    # Jaccard > 0.8，视为重复
                    is_dup = True
                    break  # 只要和一个已保留的文档重复就够了，不用继续比

            if not is_dup:
                # 不是重复，保留
                kept.append((doc, score))
                kept_terms.append(doc_terms)
            # 如果 is_dup=True，直接跳过，相当于丢弃

        return kept

    def retriever_docs(self, query: str, source_filter: list[str] | None = None) :
        """确保向量数据库正常 -> 扩展提示词 -> 抽取关键词
        -> 粗召回文档（如果有错误，检查错误，并重建向量数据库）
         -> 计算这批文档的重排分数 -> 重排分数排序后截断返回"""
        self._ensure_collection_ready()

        expanded_query = self._expand_query(query)
        query_terms = self._query_terms(query)
        search_kwargs = {"k": self.candidate_k}
        if source_filter:
            search_kwargs["filter"] = {"source": {"$in": source_filter}}

        try:
            candidates = self.vector_store.vector_store.similarity_search_with_relevance_scores(
                expanded_query,
                **search_kwargs,
            )
        except Exception as e:
            logger.error(f"向量检索失败：{str(e)}", exc_info=True)
            if self._is_corrupted_index_error(e):
                try:
                    self._repair_vector_store()
                    candidates = self.vector_store.vector_store.similarity_search_with_relevance_scores(
                        expanded_query,
                        **search_kwargs,
                    )
                except Exception as repair_error:
                    logger.error(f"重建后检索仍失败：{str(repair_error)}", exc_info=True)
                    return []
            else:
                return []

        scored_docs = []
        for doc, relevance_score in candidates:
            if relevance_score < self.min_relevance_score:
                continue
            rerank_score = self._rerank_score(query_terms, doc.page_content, relevance_score)
            doc.metadata["relevance_score"] = round(float(relevance_score), 4)
            doc.metadata["rerank_score"] = round(float(rerank_score), 4)
            scored_docs.append((doc, rerank_score))

        scored_docs.sort(key=lambda item: item[1], reverse=True)
        # 插入去重
        before_dedup = len(scored_docs)
        scored_docs = self._deduplicate_docs(scored_docs)
        logger.info(f"去重：{before_dedup} -> {len(scored_docs)}条")

        docs = [doc for doc, _ in scored_docs[: self.top_k]]

        logger.info(
            f"RAG检索完成，原始query={query}，扩展query={expanded_query},"
            f"候选数={len(candidates)}，去重前={before_dedup}，入选数={len(docs)}"
        )
        return docs

    @staticmethod
    def _format_references(docs) -> str:
        references = []
        seen = set()
        for doc in docs:
            source = doc.metadata.get("source", "未知来源")
            page = doc.metadata.get("page")
            ref = f"{source} 第{page + 1}页" if isinstance(page, int) else source
            if ref not in seen:
                seen.add(ref)
                references.append(ref)
        if not references:
            return ""
        return "\n参考来源：\n- " + "\n- ".join(references)

    def rag_summarize(self, query: str, source_filter: list[str] | None = None) -> str:
        try:
            context_docs = self.retriever_docs(query, source_filter)
        except Exception as e:
            logger.error(f"RAG检索流程异常：{str(e)}", exc_info=True)
            return "知识库检索暂时不可用，请稍后重试。"

        if not context_docs:
            return "未检索到相关参考资料。"

        context_parts = []
        for counter, doc in enumerate(context_docs, start=1):
            source = doc.metadata.get("source", "未知来源")
            page = doc.metadata.get("page")
            chunk_index = doc.metadata.get("chunk_index")
            location_parts = [f"来源={source}"]
            if page is not None:
                location_parts.append(f"页码={page}")
            if chunk_index is not None:
                location_parts.append(f"切片={chunk_index}")
            context_parts.append(
                f"[参考资料{counter}] {' | '.join(location_parts)}\n{doc.page_content.strip()}"
            )
        context = "\n\n".join(context_parts)

        return context + self._format_references(context_docs)

if __name__ == '__main__':
    rag = RagSummarizeService()

    print(rag.rag_summarize("新手应该怎么开始健身"))