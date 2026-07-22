
"""
总结服务类：用户提问，搜索参考资料，讲提问和参考资料提交给模型，让模型总结回复
"""

"""
RAG提问的完整流程
1.用户发起提问，agent调用rag_summarize工具
2.进入rag服务后，确认向量数据库和BM25检索正常运行
3.先将用户的提问变成标准术语再拓展同义词
4.然后进入两路并行的检索：向量相似度检索和BM25关键词检索
5.向量相似度根据余弦相似度算法进行计算，取相似度高于最低阈值的数据
6.BM25检索是对用户提问进行字级分词切分，再根据文档中对应词的稀有度和出现次数，在文本长度归一化的前提下做打分
7.向量相似度和BM25检索用RRF算法融合
8.最后用Jaccard去重，取前六条数据作为结果
"""
import re, threading
from app.utils.config_handler import get_chroma_config, get_synonyms_config
from app.utils.logger_handler import logger
from app.services.vector_store import VectorStoreService
from app.services.bm25_retriever import BM25Retriever
from langchain_core.documents import Document

class RagSummarizeService(object):
    def __init__(self):
        self.vector_store = VectorStoreService()
        self._collection_ready_checked = False
        self._repair_lock = threading.Lock()

        self.top_k = get_chroma_config()["k"]
        self.candidate_k = get_chroma_config().get("candidate_k", max(self.top_k * 2, self.top_k))
        self.min_relevance_score = get_chroma_config().get("min_relevance_score", 0.0)

        # ：查询扩展——当用户输入包含某个关键词时，自动把同义词也加入搜索词，提高知识库召回率。
        self.synonym_map = get_synonyms_config().get("expand", {})  # 同义词扩展
        self.normalize_map = get_synonyms_config().get("normalize", {})  # 归一化替换
        self.stopwords = get_synonyms_config().get("stopwords", set())  # 停用词

        self.bm25 = BM25Retriever()

    def _ensure_collection_ready(self):
        if self._collection_ready_checked:
            return
        self._collection_ready_checked = True

        try:
            current_count = self.vector_store.vector_store._collection.count()
        except Exception as e:
            logger.error(f"获取向量库文档数量失败：{str(e)}", exc_info=True)
            current_count = 0

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

    @staticmethod
    def _document_terms(content: str) -> set[str]:
        return set(re.findall(r"[一-鿿]{2,}|[a-z0-9]+", content.lower()))

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

    def _sync_bm25_index(self):
        """检查BM25索引是否过期，过期则从ChromaDB重建"""
        try:
            # 拿到 ChromaDB 当前文档总数
            current_count = self.vector_store.vector_store._collection.count()
        except Exception as e:
            logger.error(f"获取ChromaDB文档数失败：{str(e)}")
            return

        # 索引还是新鲜的吗
        if not self.bm25.is_stale(current_count):
            return

        logger.info("BM25索引过期或未构建，准备从ChromaDB同步...")
        try:
            # 从 ChromaDB 拉取全量文档
            raw = self.vector_store.vector_store.get(
                include=["documents", "metadatas"]
            )
            # 把字符串列表 + 元数据列表 组装成 LangChain Document 对象列表
            #   zip(["深蹲是...", "减脂需要..."], [{"source": "a.txt"}, {"source": "b.txt"}])
            #   → ("深蹲是...", {"source": "a.txt"}), ("减脂需要...", {"source": "b.txt"})
            documents = [
                Document(page_content=text, metadata=meta or {})
                for text, meta in zip(raw["documents"], raw["metadatas"])
            ]
            self.bm25.build(documents)
        except Exception as e:
            logger.error(f"BM25索引同步失败：{str(e)}", exc_info=True)

    @staticmethod
    def _rrf_fusion(vector_results: list[tuple], bm25_results: list[tuple], k: int = 60) -> list[tuple]:
        """RRF双路融合：按排名合并向量检索和BM25检索结果"""
        """
            vector_results 格式: [(doc_A, 0.92), (doc_B, 0.87), (doc_C, 0.74)]
            enumerate(..., start=1): rank 从 1 开始，不是 0
                rank=1 → doc_A
                rank=2 → doc_B
                rank=3 → doc_C
            key 用 (source, chunk_index) 作为文档的唯一标识
        """
        vector_rank_map = {}
        for rank, (doc, _) in enumerate(vector_results, start=1):
            key = (doc.metadata.get("source", ""), doc.metadata.get("chunk_index", -1))
            vector_rank_map[key] = rank

        bm25_rank_map = {}
        for rank, (doc, _) in enumerate(bm25_results, start=1):
            key = (doc.metadata.get("source", ""), doc.metadata.get("chunk_index", -1))
            bm25_rank_map[key] = rank

        # 用字典去重：同一个 key 只保留一份 Document 对象
        all_docs = {}
        for doc, _ in vector_results:
            key = (doc.metadata.get("source", ""), doc.metadata.get("chunk_index", -1))
            all_docs[key] = doc
        for doc, _ in bm25_results:
            key = (doc.metadata.get("source", ""), doc.metadata.get("chunk_index", -1))
            all_docs[key] = doc

        # 对每个唯一文档计算 RRF 分数
        scored = []
        for key, doc in all_docs.items():
            rrf_score = 0.0
            if key in vector_rank_map:
                rrf_score += 1.0 / (k + vector_rank_map[key])
            if key in bm25_rank_map:
                rrf_score += 1.0 / (k + bm25_rank_map[key])
            scored.append((doc, rrf_score))

        # 按 RRF 总分降序排列
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def retriever_docs(self, query: str, source_filter: list[str] | None = None) :
        """确保向量数据库正常 -> 扩展提示词 -> 抽取关键词
        -> 粗召回文档（如果有错误，检查错误，并重建向量数据库）
         -> 计算这批文档的重排分数 -> 重排分数排序后截断返回"""
        self._ensure_collection_ready()
        self._sync_bm25_index()

        expanded_query = self._expand_query(query)
        search_kwargs = {"k": self.candidate_k}
        if source_filter:
            search_kwargs["filter"] = {"source": {"$in": source_filter}}

        # 向量检索
        try:
            vector_candidates = self.vector_store.vector_store.similarity_search_with_relevance_scores(
                expanded_query,
                **search_kwargs,
            )
        except Exception as e:
            logger.error(f"向量检索失败：{str(e)}", exc_info=True)
            if self._is_corrupted_index_error(e):
                try:
                    self._repair_vector_store()
                    vector_candidates = self.vector_store.vector_store.similarity_search_with_relevance_scores(
                        expanded_query,
                        **search_kwargs,
                    )
                except Exception as repair_error:
                    logger.error(f"重建后检索仍失败：{str(repair_error)}", exc_info=True)
                    vector_candidates = []
            else:
                vector_candidates = []

        # 过滤低相关度
        vector_results = [
            (doc, score) for doc, score in vector_candidates
            if score >= self.min_relevance_score
        ]

        # BM 25关键词检索（不受 source_filter 影响，BM25 不做过滤）
        bm25_results = self.bm25.search(expanded_query, k=self.candidate_k)

        # RRF 双路融合
        scored_docs = self._rrf_fusion(vector_results, bm25_results)

        # Jaccard 去重
        before_dedup = len(scored_docs)
        scored_docs = self._deduplicate_docs(scored_docs)
        logger.info(f"去重：{before_dedup} -> {len(scored_docs)}条")

        # 取 top_k
        docs = [doc for doc, _ in scored_docs[:self.top_k]]

        logger.info(
            f"RAG检索完成，原始query={query}，扩展query={expanded_query}，"
            f"向量召回={len(vector_results)}，BM25召回={len(bm25_results)}，"
            f"融合后={before_dedup}，入选={len(docs)}"
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