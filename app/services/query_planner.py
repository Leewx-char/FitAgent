"""受控的在线检索查询理解与拆解。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Protocol

from app.services.factory import get_chat_model
from app.utils.logger_handler import logger


class QueryModel(Protocol):
    """查询规划器依赖的最小聊天模型接口。"""

    def invoke(self, prompt: str): ...


@dataclass(frozen=True)
class QueryPlan:
    """一次查询理解的输出，最多保留两个检索子查询。"""

    original_query: str
    search_queries: tuple[str, ...]
    rewritten_query: str
    used_llm: bool
    fallback_reason: str = ""


class QueryPlanner:
    """只在指代或复合问题时调用 LLM，普通问题保持单查询快速路径。"""

    _REFERENTIAL_PATTERN = re.compile(r"^(?:那[个]?|它|这个|那个|上述|前面|然后|分别).{0,18}$")
    _COMPOUND_PATTERN = re.compile(r"(?:以及|同时|分别|怎么练.*怎么吃|训练.*饮食|饮食.*训练)")

    def __init__(
        self,
        model_factory: Callable[[], QueryModel] = get_chat_model,
        history_turns: int = 3,
        max_subqueries: int = 2,
    ) -> None:
        self._model_factory = model_factory
        self.history_turns = history_turns
        self.max_subqueries = max_subqueries

    def plan(self, query: str, history: list[dict] | None = None) -> QueryPlan:
        """将需要上下文理解的问题转成有限、可回退的检索查询。"""

        normalized_query = re.sub(r"\s+", " ", query).strip()
        normalized_history = self._normalize_history(history or [])
        if not self._needs_planning(normalized_query, normalized_history):
            return QueryPlan(normalized_query, (normalized_query,), normalized_query, False)

        try:
            response = self._model_factory().invoke(
                self._build_prompt(normalized_query, normalized_history)
            )
            content = self._content_to_text(response)
            rewritten_query, subqueries = self._parse_response(content, normalized_query)
            return QueryPlan(
                original_query=normalized_query,
                search_queries=subqueries,
                rewritten_query=rewritten_query,
                used_llm=True,
            )
        except Exception as error:
            logger.warning("查询规划失败，回退原查询：%s", error)
            return QueryPlan(
                normalized_query,
                (normalized_query,),
                normalized_query,
                False,
                fallback_reason=type(error).__name__,
            )

    def _needs_planning(self, query: str, history: list[dict]) -> bool:
        has_reference = bool(history) and bool(self._REFERENTIAL_PATTERN.search(query))
        has_compound_question = bool(self._COMPOUND_PATTERN.search(query))
        return has_reference or has_compound_question

    def _normalize_history(self, history: list[dict]) -> list[dict[str, str]]:
        messages = []
        for item in history[-self.history_turns * 2 :]:
            role = item.get("role")
            content = re.sub(r"\s+", " ", str(item.get("content", ""))).strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content[:400]})
        return messages

    def _build_prompt(self, query: str, history: list[dict[str, str]]) -> str:
        history_text = "\n".join(f"{item['role']}: {item['content']}" for item in history)
        return f"""你是中文健身知识库的检索查询规划器。只输出 JSON，不要 Markdown。
根据历史消息补全当前问题的指代；若问题同时包含训练和饮食等独立主题，最多拆成两个可独立检索的子查询。
不要回答问题，不要新增未出现的事实。普通问题只保留一个子查询。

历史消息：
{history_text or "无"}

当前问题：{query}

输出格式：{{"rewritten_query":"完整检索问题","subqueries":["子查询1"]}}"""

    @staticmethod
    def _content_to_text(response: object) -> str:
        content = getattr(response, "content", response)
        if isinstance(content, list):
            return "".join(
                item.get("text", "") if isinstance(item, dict) else str(item) for item in content
            ).strip()
        return str(content).strip()

    def _parse_response(self, content: str, original_query: str) -> tuple[str, tuple[str, ...]]:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise ValueError("查询规划器未返回 JSON")
        payload = json.loads(match.group(0))
        rewritten = re.sub(r"\s+", " ", str(payload.get("rewritten_query", ""))).strip()
        if not rewritten:
            raise ValueError("查询规划器未返回 rewritten_query")
        raw_subqueries = payload.get("subqueries", [])
        if not isinstance(raw_subqueries, list):
            raise ValueError("查询规划器 subqueries 格式错误")
        candidates = [rewritten, *[str(item).strip() for item in raw_subqueries]]
        unique = tuple(
            dict.fromkeys(item[:200] for item in candidates if item and len(item) <= 200)
        )[: self.max_subqueries]
        return rewritten, unique or (original_query,)
