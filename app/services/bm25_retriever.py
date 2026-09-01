import threading
import json
from pathlib import Path
from rank_bm25 import BM25Okapi
from app.utils.logger_handler import logger
import re
from langchain_core.documents import Document


class BM25Retriever:
    def __init__(self):
        """初始化空的线程安全 BM25 索引及文档快照。"""
        self._index = None
        self._docs = []
        self._lock = threading.Lock()
        self._doc_count_snapshot = 0

    @staticmethod
    # 分词
    def _tokenize(text: str) -> list[str]:
        """按中文单字及英文或数字连续片段切分检索词。"""
        tokens = []
        # 正则匹配：连续的中文字符 / 连续的英文 /数字
        for chunk in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", text.lower()):
            if re.match(r"[\u4e00-\u9fff]", chunk):
                # chunk 是中文，比如 “深蹲标准动作”
                # 拆成单个字：["深", "蹲", "标", "准", "动", "作"]
                tokens.extend(list(chunk))
            else:
                # chunk 是英文/数字，比如 “bmi“ 或 “25”
                # 保持整体
                tokens.append(chunk)
        return tokens

    # 判断索引是否过期
    def is_stale(self, current_doc_count: int) -> bool:
        """根据当前文档数量判断索引是否未建或过期。"""
        return self._index is None or self._doc_count_snapshot != current_doc_count

    def build(self, documents: list):
        """从文档切词构建 BM25 索引并保存原始文档。"""
        with self._lock:
            self._docs = list(documents)  # 保存原始 Document 对象，后面检索时需要返回
            tokenized = [self._tokenize(doc.page_content) for doc in documents]
            self._index = BM25Okapi(tokenized)
            self._doc_count_snapshot = len(documents)
            logger.info(f"BM25索引构建完成，共 {len(documents)} 篇文档")

    def load_artifact(self, artifact_path: str) -> str | None:
        """加载离线产出的文档工件；请求期间绝不从 Qdrant 重建。"""
        path = Path(artifact_path)
        if not path.exists():
            logger.warning(f"BM25 工件不存在，当前仅使用向量检索：{artifact_path}")
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw_documents = data.get("documents", [])
            documents = [
                Document(
                    page_content=item["page_content"],
                    metadata=item.get("metadata", {}),
                )
                for item in raw_documents
                if item.get("page_content")
            ]
            if not documents:
                logger.warning("BM25 工件没有有效文档，当前仅使用向量检索")
                return None
            self.build(documents)
            return data.get("index_revision")
        except (OSError, ValueError, KeyError, TypeError) as error:
            logger.warning(f"BM25 工件加载失败，当前仅使用向量检索：{error}")
            return None

    def search(
        self, query: str, k: int = 15, source_filter: list[str] | None = None
    ) -> list[tuple]:
        """按 BM25 分数检索文档，并可按来源筛选。"""
        if self._index is None:
            return []

        tokenized_query = self._tokenize(query)
        scores = self._index.get_scores(tokenized_query)
        # scores 是一个 list[float]，长度 == len(self._docs), 顺序一一对应

        # 配对并排序
        scored = sorted(zip(self._docs, scores), key=lambda x: x[1], reverse=True)
        if source_filter:
            allowed_sources = set(source_filter)
            scored = [
                (doc, score)
                for doc, score in scored
                if doc.metadata.get("source_id", doc.metadata.get("source")) in allowed_sources
            ]
        return [
            (
                Document(
                    page_content=str(doc.metadata.get("parent_text") or doc.page_content),
                    metadata={**doc.metadata, "child_text": doc.page_content},
                ),
                score,
            )
            for doc, score in scored[:k]
        ]
