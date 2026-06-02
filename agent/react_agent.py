import re
import json
from typing import Iterable
from langchain.agents import create_agent
from langchain_core.messages import AIMessageChunk, ToolMessage

from model.factory import get_chat_model
from utils.prompt_loader import load_system_prompts
from agent.tools.agent_tools import (rag_summarize,
get_weather, get_user_location, get_user_id, trigger_report,
get_current_month, get_user_profile)
from agent.tools.middleware import monitor_tool, log_before_model, report_prompt_switch

TOOL_DISPLAY = {
    "get_user_profile": "获取用户画像",
    "rag_summarize": "检索知识库",
    "get_weather": "查询天气",
    "get_user_location": "获取位置",
    "get_current_month": "获取月份",
    "get_user_id": "获取用户ID",
    "trigger_report": "生成报告",
}

class ReactAgent:

    COMMON_CITIES = [
        "北京", "上海", "广州", "深圳", "杭州", "苏州", "南京", "成都", "重庆", "天津",
        "武汉", "西安", "长沙", "青岛", "宁波", "厦门", "郑州", "合肥", "福州", "济南",
    ]

    INVALID_CITY_VALUES = {"哪个城市", "什么城市", "哪座城市", "哪个市", "哪里", "哪儿"}

    def __init__(self):
        self.agent = create_agent(
            model=get_chat_model(),
            system_prompt=load_system_prompts(),
            tools=[rag_summarize, get_weather, get_user_location,get_user_id,
                   get_current_month, get_user_profile, trigger_report
            ],
            middleware=[monitor_tool, log_before_model, report_prompt_switch]
        )

    @staticmethod
    def _normalize_messages(messages: Iterable[dict]) -> list[dict]:
        normalized = []
        for message in messages:
            role = message.get("role")
            content = (message.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            normalized.append({"role": role, "content": content})
        return normalized

    @classmethod
    def _extract_session_facts(cls, messages: list[dict]) -> dict:
        facts = {}

        for message in messages:
            content = (message.get("content") or "").strip()
            if not content:
                continue

            for city in cls.COMMON_CITIES:
                if city in content:
                    facts["city"] = city

            city_match = re.search(
                r"(?:在|住在|来自|位于)([^\s，。！？,.!?]{2,12}(?:市|"
                r"县|区|北京|上海|广州|深圳|杭州|苏州|南京|"
                r"成都|重庆|天津|武汉|西安|长沙|青岛|宁波|厦门"
                r"|郑州|合肥|福州|济南))",
                content,
            )
            if city_match:
                candidate_city = city_match.group(1)
                if candidate_city not in cls.INVALID_CITY_VALUES:
                    facts["city"] = candidate_city
        return facts

    def execute_stream(self, messages: list[dict], user_id: int | None = None, city: str = ""):
        normalized_messages = self._normalize_messages(messages)
        session_facts = self._extract_session_facts(normalized_messages)
        input_dict = {"messages": normalized_messages}

        run_context = {"report": False, "session_facts": session_facts}
        if user_id:
            run_context["user_id"] = user_id
        if city:
            run_context["city"] = city

        seen_tool_ids = set()  # 记录已见过的工具调用ID，用于去重和判断"是否调过工具"
        last_tool_step = None  # 记录最后一个 ToolMessage 所在的 step 编号
        # None 表示：还没执行完所有工具调用（还在调工具阶段）

        for msg_chunk, metadata in self.agent.stream(
                input_dict, stream_mode="messages", context=run_context
        ):
            if isinstance(msg_chunk, AIMessageChunk):
                tool_call_chunks = getattr(msg_chunk, "tool_call_chunks", None) or []
                # 工具调用通知
                for tc_chunk in tool_call_chunks:
                    if tc_chunk.get("name") and tc_chunk.get("id"):
                        if tc_chunk["id"] not in seen_tool_ids:
                            seen_tool_ids.add(tc_chunk["id"])
                            last_tool_step = None # ← 关键：见到新工具调用，重置为 None
                            yield json.dumps(
                                {"type": "tool", "name": TOOL_DISPLAY.get(tc_chunk["name"], tc_chunk["name"])},
                                ensure_ascii=False) + "\n" # 前端显示"🔍 获取画像..."
                # 文本内容
                if msg_chunk.content:
                    if not seen_tool_ids or (
                            last_tool_step is not None and metadata.get("langgraph_step", 0) > last_tool_step):
                        yield json.dumps({"type": "text", "content": msg_chunk.content}, ensure_ascii=False) + "\n"
            elif isinstance(msg_chunk, ToolMessage):
                last_tool_step = metadata.get("langgraph_step", 0)

if __name__ == '__main__':
    agent = ReactAgent()
    res = agent.execute_stream([{"role": "user", "content": "我想减脂，应该怎么练？"}])
    for chunk in res:
        print(chunk, end="", flush=True)