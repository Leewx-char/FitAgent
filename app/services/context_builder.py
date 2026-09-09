"""在不调用额外 LLM 的前提下控制 RAG 上下文预算。"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.retrieval_contracts import RetrievalHit


@dataclass(frozen=True)
class ContextSnippet:
    """一个可引用证据在上下文预算内的展示文本。"""

    evidence_id: str
    text: str
    truncated: bool


class ContextBuilder:
    """按最终排名裁剪切片正文，避免长上下文挤掉其他证据。"""

    def __init__(self, max_context_chars: int = 6000, max_chars_per_evidence: int = 1200) -> None:
        """设置整体上下文与单条证据的字符预算。"""
        self.max_context_chars = max_context_chars
        self.max_chars_per_evidence = max_chars_per_evidence

    def build(self, hits: tuple[RetrievalHit, ...]) -> list[ContextSnippet]:
        """按排名分配总预算，预算耗尽后停止追加低优先级证据。"""

        snippets = []
        remaining = self.max_context_chars
        for hit in hits:
            if remaining <= 0:
                break
            budget = min(self.max_chars_per_evidence, remaining)
            text, truncated = self._clip_text(hit.text, budget)
            snippets.append(ContextSnippet(hit.evidence_id, text, truncated))
            remaining -= len(text)
        return snippets

    @staticmethod
    def _clip_text(text: str, budget: int) -> tuple[str, bool]:
        """在字符预算内保留正文前缀，并为截断标记预留空间。"""
        if budget <= 0:
            return "", bool(text)
        if len(text) <= budget:
            return text, False
        if budget <= 2:
            return text[:budget], True
        return text[: budget - 2].rstrip() + "……", True
