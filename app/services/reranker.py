"""Qdrant 首阶段候选的 DashScope 二阶段排序边界。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import dashscope

from app.services.vector_repository import ScoredChunk


class Reranker(Protocol):
    """只接收已召回的候选，并返回最终排序后的证据。"""

    def rerank(
        self, query: str, candidates: Sequence[ScoredChunk], *, limit: int
    ) -> list[ScoredChunk]:
        """按查询相关性重排候选，最多返回 ``limit`` 条。"""
        ...


class DashScopeReranker:
    """通过 DashScope 文本排序模型重排小规模 Qdrant 候选集。"""

    def __init__(self, model_name: str, api_key: str) -> None:
        self.model_name = model_name
        self.api_key = api_key

    def rerank(
        self, query: str, candidates: Sequence[ScoredChunk], *, limit: int
    ) -> list[ScoredChunk]:
        """将候选正文发送给排序模型，仅保留其返回的索引和分数。"""
        if limit <= 0 or not candidates:
            return []
        response = dashscope.TextReRank.call(
            model=self.model_name,
            query=query,
            documents=[candidate.document.page_content for candidate in candidates],
            top_n=min(limit, len(candidates)),
            return_documents=False,
            api_key=self.api_key,
        )
        if response.status_code != 200:
            raise RuntimeError(f"DashScope 重排暂时不可用：{response.code}")
        results = getattr(getattr(response, "output", None), "results", None)
        if results is None:
            raise RuntimeError("DashScope 重排返回缺少结果。")

        selected: list[ScoredChunk] = []
        seen_indexes: set[int] = set()
        for item in results:
            if len(selected) >= min(limit, len(candidates)):
                raise RuntimeError("DashScope 重排返回了超出请求上限的候选。")
            index = item.index
            if not isinstance(index, int) or index < 0 or index >= len(candidates):
                raise RuntimeError("DashScope 重排返回无效候选索引。")
            if index in seen_indexes:
                raise RuntimeError("DashScope 重排返回重复候选索引。")
            seen_indexes.add(index)
            selected.append(
                ScoredChunk(
                    document=candidates[index].document,
                    score=float(item.relevance_score),
                )
            )
        return selected
