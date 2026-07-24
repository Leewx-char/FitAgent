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
    """优先保留命中子片段附近的父段内容，避免长上下文挤掉其他证据。"""

    def __init__(self, max_context_chars: int = 6000, max_chars_per_evidence: int = 1200) -> None:
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
            text, truncated = self._clip_around_child_text(hit.text, hit.child_text, budget)
            snippets.append(ContextSnippet(hit.evidence_id, text, truncated))
            remaining -= len(text)
        return snippets

    @staticmethod
    def _clip_around_child_text(parent_text: str, child_text: str, budget: int) -> tuple[str, bool]:
        if budget <= 0:
            return "", bool(parent_text)
        if len(parent_text) <= budget:
            return parent_text, False
        index = parent_text.find(child_text)
        if index < 0:
            if budget <= 2:
                return parent_text[:budget], True
            budget -= 2
            return parent_text[:budget].rstrip() + "……", True
        if budget <= 4:
            return parent_text[:budget], True
        # Reserve room for both boundary markers before choosing the text
        # window so the configured context budget remains a hard limit.
        budget -= 4
        start = max(0, index - budget // 3)
        end = min(len(parent_text), start + budget)
        start = max(0, end - budget)
        prefix = "……" if start else ""
        suffix = "……" if end < len(parent_text) else ""
        return prefix + parent_text[start:end].strip() + suffix, True
