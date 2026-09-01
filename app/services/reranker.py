"""在线 RAG V1 的可回退轻量重排序器。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RerankCandidate:
    """重排序器所需的最小候选信息。"""

    candidate_id: str
    text: str
    base_score: float


@dataclass(frozen=True)
class RerankResult:
    """重排序后的候选标识与融合分数。"""

    candidate_id: str
    score: float


class Reranker(Protocol):
    """候选集精排边界；未来可替换为 Cross-Encoder 或云端 rerank API。"""

    def rerank(self, query: str, candidates: list[RerankCandidate]) -> list[RerankResult]:
        """按查询对候选集精排并返回融合分数。"""
        ...


class LexicalReranker:
    """用查询词覆盖率微调 RRF 顺序，避免在 V1 再引入一套模型运行时。"""

    def __init__(self, base_score_weight: float = 0.7) -> None:
        """设置原始 RRF 分数在最终精排分数中的权重。"""
        self.base_score_weight = base_score_weight

    @staticmethod
    def _terms(text: str) -> set[str]:
        """提取英文数字词与中文单字构成的去重词集。"""
        terms = set(re.findall(r"[a-z0-9]+|[一-鿿]", text.lower()))
        return terms

    def rerank(self, query: str, candidates: list[RerankCandidate]) -> list[RerankResult]:
        """保留 RRF 为主导，以命中子片段的词覆盖率进行稳定微调。"""

        if not candidates:
            return []
        query_terms = self._terms(query)
        max_base_score = max(candidate.base_score for candidate in candidates) or 1.0
        results = []
        for candidate in candidates:
            candidate_terms = self._terms(candidate.text)
            lexical_score = len(query_terms & candidate_terms) / max(len(query_terms), 1)
            normalized_base_score = candidate.base_score / max_base_score
            score = (
                self.base_score_weight * normalized_base_score
                + (1 - self.base_score_weight) * lexical_score
            )
            results.append(RerankResult(candidate.candidate_id, round(score, 8)))
        return sorted(results, key=lambda item: item.score, reverse=True)
