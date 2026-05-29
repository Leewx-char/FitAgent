
"""
总结服务类：用户提问，搜索参考资料，讲提问和参考资料提交给模型，让模型总结回复
"""
import re, threading
from utils.config_handler import chroma_conf
from utils.logger_handler import logger
from langchain_core.output_parsers import StrOutputParser
from rag.vector_store import VectorStoreService
from utils.prompt_loader import load_rag_prompts
from langchain_core.prompts import PromptTemplate
from model.factory import get_chat_model

class RagSummarizeService(object):
    def __init__(self):
        self.vector_store = VectorStoreService()
        self._collection_ready_checked = False
        self._repair_lock = threading.Lock()

        self.prompt_text = load_rag_prompts()
        self.prompt_template = PromptTemplate.from_template(self.prompt_text)
        self.model = get_chat_model()
        self.chain = self._init_chain()

        self.top_k = chroma_conf["k"]
        self.candidate_k = chroma_conf.get("candidate_k", max(self.top_k * 2, self.top_k))
        self.min_relevance_score = chroma_conf.get("min_relevance_score", 0.0)

        self.synonym_map = {
            "不回充": ["回充失败", "无法返回充电座", "找不到充电座"],
            "回不了充": ["回充失败", "无法返回充电器"],
            "迷路": ["定位异常", "建图异常", "导航异常"],
            "漏扫": ["清扫遗漏", "覆盖率低"],
            "水痕": ["拖地水痕", "拖布湿度", "地面残留水渍"],
        }

        self.stopwords = {
            "的", "了", "呢", "吗", "呀", "啊", "我", "想", "请问", "一下", "怎么", "怎样",
            "是否", "一个", "这个", "那个", "可以", "需要", "有没有", "如何", "机器人", "扫地机器人",
        }


    def _init_chain(self):
        """
        构建处理链：Prompt填充 → 打印调试 → LLM推理 → 字符串解析
        Chain流程：
        PromptTemplate → print_prompt → chat_model → StrOutputParser
        """
        chain = self.prompt_template | self.model | StrOutputParser()
        return chain

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

    @staticmethod
    def _normalize_query(query: str) -> str:
        normalized = re.sub(r"\s+", " ", query.strip().lower())
        replacements = {
            "扫拖一体": "扫拖一体机器人",
            "回充座": "充电座",
            "基站": "充电座",
            "回基站": "回充",
        }
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

    def retriever_docs(self, query: str) :
        """确保向量数据库正常 -> 扩展提示词 -> 抽取关键词
        -> 粗召回文档（如果有错误，检查错误，并重建向量数据库）
         -> 计算这批文档的重排分数 -> 重排分数排序后截断返回"""
        self._ensure_collection_ready()

        expanded_query = self._expand_query(query)
        query_terms = self._query_terms(query)

        try:
            candidates = self.vector_store.vector_store.similarity_search_with_relevance_scores(
                expanded_query,
                k=self.candidate_k,
            )
        except Exception as e:
            logger.error(f"向量检索失败：{str(e)}", exc_info=True)
            if self._is_corrupted_index_error(e):
                try:
                    self._repair_vector_store()
                    candidates = self.vector_store.vector_store.similarity_search_with_relevance_scores(
                        expanded_query,
                        k=self.candidate_k,
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
        docs = [doc for doc, _ in scored_docs[: self.top_k]]

        logger.info(
            f"RAG检索完成，原始query={query}，扩展query={expanded_query},"
            f"候选数={len(candidates)}，入选数={len(docs)}"
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

    def rag_summarize(self, query: str) -> str:
        try:
            context_docs = self.retriever_docs(query)
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

        try:
            answer = self.chain.invoke({"input": query, "context": context})
            return answer.strip() + self._format_references(context_docs)
        except Exception as e:
            logger.error(f"RAG总结失败：{str(e)}", exc_info=True)
            return "知识总结暂时不可用，请稍后重试"

if __name__ == '__main__':
    rag = RagSummarizeService()

    print(rag.rag_summarize("小户型适合哪些扫地机器人"))