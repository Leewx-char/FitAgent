import threading
from rank_bm25 import BM25Okapi
from app.utils.logger_handler import logger
import re

class BM25Retriever:
    def __init__(self):
        self._index = None
        self._docs = []
        self._lock = threading.Lock()
        self._doc_count_snapshot = 0

    @staticmethod
    # 分词
    def _tokenize(text: str) -> list[str]:
        tokens = []
        # 正则匹配：连续的中文字符 / 连续的英文 /数字
        for chunk in re.findall(r'[\u4e00-\u9fff]+|[a-z0-9]+', text.lower()):
            if re.match(r'[\u4e00-\u9fff]', chunk):
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
        return self._index is None or self._doc_count_snapshot != current_doc_count

    def build(self, documents: list):
        with self._lock:
            self._docs = list(documents) # 保存原始 Document 对象，后面检索时需要返回
            tokenized = [self._tokenize(doc.page_content) for doc in documents]
            self._index = BM25Okapi(tokenized)
            self._doc_count_snapshot = len(documents)
            logger.info(f"BM25索引构建完成，共 {len(documents)} 篇文档")

    def search(self, query: str, k: int = 15) -> list[tuple]:
        if self._index is None:
            return []

        tokenized_query = self._tokenize(query)
        scores = self._index.get_scores(tokenized_query)
        # scores 是一个 list[float]，长度 == len(self._docs), 顺序一一对应

        # 配对并排序
        scored = sorted(
            zip(self._docs, scores),
            key=lambda x: x[1],
            reverse=True
        )
        return scored[:k]