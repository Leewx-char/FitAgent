from datetime import datetime, date
import json
import time
import threading
from collections.abc import Mapping
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import URLError
from functools import lru_cache, wraps
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command
from app.services.rag_service import RagSummarizeService
from app.utils.logger_handler import logger
from app.core.database import get_db_session
from app.models import UserProfile
from app.services.fitness_insights import (
    list_activity_candidates,
    load_activity_snapshot,
    load_fitness_snapshot,
)
from app.services.memory_service import MemoryService
from app.core.settings import get_settings


@lru_cache(maxsize=1)
def _get_rag_service() -> RagSummarizeService:
    """复用已加载 BM25 工件的 RAG 服务；构造过程不执行 embedding 或 Qdrant 查询。"""

    return RagSummarizeService()


def warm_rag_retriever() -> str | None:
    """在应用启动阶段加载 BM25 工件，消除首个 RAG 请求的本地建索引延迟。"""
    service = _get_rag_service()
    logger.info("RAG 预热完成：BM25 revision=%s", service.bm25_revision or "unavailable")
    return service.bm25_revision


def build_evidence_cards(result) -> list[dict[str, str | int | float | None]]:
    """将命中证据裁剪为适合 SSE 传输和前端展示的非敏感卡片数据。"""
    if result is None:
        return []

    cards = []
    for hit in result.hits:
        snippet = " ".join(hit.child_text.split())
        cards.append(
            {
                "rank": hit.rank,
                "evidence_id": hit.evidence_id,
                "source_id": hit.source_id,
                "snippet": snippet[:240] + ("…" if len(snippet) > 240 else ""),
                "tags": str(hit.metadata.get("tags", "")),
                "score": hit.rerank_score if hit.rerank_score is not None else hit.score,
            }
        )
    return cards


def _runtime_context_value(runtime: ToolRuntime, name: str, default=None):
    """从当前工具运行时读取可信请求字段，并兼容迁移期的映射上下文。"""
    context = runtime.context
    if isinstance(context, Mapping):
        return context.get(name, default)
    return getattr(context, name, default)


def _tool_state_command(content: str, runtime: ToolRuntime, **state_update: object) -> Command:
    """返回工具消息并把短期产物写回本次 Agent 状态。"""
    return Command(
        update={
            "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id)],
            **state_update,
        }
    )


_NETWORK_ERRORS = (URLError, ConnectionError, TimeoutError, OSError)


def _degradation_json(
    message: str, suggestion: str = "可以跳过此工具，基于已有信息继续回复，或提示用户稍后重试"
) -> str:
    """降级兜底：外部服务不可用时返回结构化错误，供 LLM 自然融入回复。"""
    return json.dumps(
        {
            "status": "error",
            "message": message,
            "suggestion": suggestion,
        },
        ensure_ascii=False,
    )


def _with_retry(max_retries: int = 1, delay: float = 1.0):
    """纯重试层：对外部 API 的瞬时故障重试 N 次。只重试网络类异常，
    业务异常直接透传；最终仍失败则 re-raise，交给外层熔断/降级处理。"""

    def decorator(func):
        """返回为目标函数配置网络重试的装饰器。"""

        @wraps(func)
        def wrapper(*args, **kwargs):
            """执行目标函数，并仅对网络错误按次数重试。"""
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except _NETWORK_ERRORS as e:
                    if attempt < max_retries:
                        logger.warning(
                            f"工具 {func.__name__} 调用失败（第{attempt + 1}次），"
                            f"{delay}秒后重试：{str(e)}"
                        )
                        time.sleep(delay)
                    else:
                        logger.error(f"工具 {func.__name__} 重试{max_retries}次后仍失败：{str(e)}")
                        raise  # 交给外层熔断器记账 + 降级

        return wrapper

    return decorator


class CircuitBreaker:
    """熔断器：连续失败达阈值后置 OPEN（快速失败，不再真调外部服务），
    冷却期满进 HALF_OPEN 放行一次试探，成功→CLOSED 恢复，失败→重新 OPEN。

    状态机：CLOSED --失败达阈值--> OPEN --冷却超时--> HALF_OPEN --成功--> CLOSED
                                                          └--失败--> OPEN
    """

    def __init__(self, name: str, failure_threshold: int = 3, recovery_timeout: float = 30.0):
        """初始化指定服务名、失败阈值和恢复窗口的熔断器。"""
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = "closed"
        self.failure_count = 0
        self.opened_at = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """是否放行本次调用。OPEN 且冷却未满 → 拒绝（快速失败）。"""
        with self._lock:
            if self.state == "open":
                if time.time() - self.opened_at >= self.recovery_timeout:
                    self.state = "half_open"  # 冷却结束，放一次试探
                    return True
                return False
            return True  # closed / half_open

    def on_success(self):
        """在调用成功后清空失败计数并关闭熔断器。"""
        with self._lock:
            self.failure_count = 0
            self.state = "closed"

    def on_failure(self):
        """记录一次失败，达到阈值或半开探测失败时打开熔断器。"""
        with self._lock:
            self.failure_count += 1
            # HALF_OPEN 下试探失败，或 CLOSED 下累计到阈值 → OPEN
            if self.state == "half_open" or self.failure_count >= self.failure_threshold:
                self.state = "open"
                self.opened_at = time.time()


def _with_circuit_breaker(name: str, failure_threshold: int = 3, recovery_timeout: float = 30.0):
    """创建独立的熔断和降级装饰器；打开时快速失败，成功时恢复。"""
    breaker = CircuitBreaker(name, failure_threshold, recovery_timeout)

    def decorator(func):
        """返回为目标函数配置熔断和降级响应的装饰器。"""

        @wraps(func)
        def wrapper(*args, **kwargs):
            """受熔断器保护地调用目标函数，并在网络失败时降级。"""
            if not breaker.allow():
                logger.warning(f"熔断器[{name}]OPEN，快速失败，跳过真实调用")
                return _degradation_json(
                    f"服务[{name}]连续失败已熔断，冷却中",
                    "暂时跳过此工具，基于已有信息继续回复，稍后会自动尝试恢复",
                )
            try:
                result = func(*args, **kwargs)
            except _NETWORK_ERRORS as e:
                breaker.on_failure()
                logger.error(
                    f"熔断器[{name}]记录失败(state={breaker.state}, "
                    f"count={breaker.failure_count})：{str(e)}"
                )
                return _degradation_json(f"服务[{name}]暂时不可用：{str(e)}")
            else:
                breaker.on_success()
                return result

        return wrapper

    return decorator


def _request_json(base_url: str, params: dict) -> dict:
    """以查询参数请求 URL 并解析 JSON 响应。"""
    url = f"{base_url}?{urlencode(params)}"
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


SOURCE_MAP = {
    "动作指南": ["动作指南大全.txt"],
    "营养学": ["营养学知识.txt"],
    "训练计划": ["训练计划指南.txt"],
    "损伤预防": ["运动损伤预防.txt"],
    "基础知识": ["健身基础知识.txt"],
}


@tool(
    description="从知识库检索专业资料原始片段。可选通过source指定领域缩小范围：动作指南、营养学、训练计划、损伤预防、基础知识"
)
@_with_circuit_breaker(name="rag_summarize")
@_with_retry()
def rag_summarize(query: str, runtime: ToolRuntime, source: str = "") -> Command:
    """检索问题并保存可展示证据，返回受预算约束的上下文。"""
    source_filter = SOURCE_MAP.get(source) if source else None
    rag_context = _get_rag_service().build_context(
        query,
        source_filter,
        runtime.state.get("retrieval_history", []),
    )
    evidence = build_evidence_cards(rag_context.result)
    return _tool_state_command(
        rag_context.content,
        runtime,
        rag_evidence=evidence,
    )


@tool(description="获取指定城市的实时天气信息，返回温度、体感温度、降水、风速等数据")
@_with_circuit_breaker(name="get_weather")
@_with_retry()
def get_weather(city: str):
    """查询城市地理位置和天气，并返回面向 Agent 的 JSON 结果。"""
    city = city.strip()
    if not city:
        return json.dumps(
            {"status": "error", "message": "城市不能为空", "suggestion": "请提供有效的城市名称"},
            ensure_ascii=False,
        )

    # 网络异常由 @_with_retry 兜底重试，这里只处理业务异常
    response = _request_json(
        "https://api.weatherstack.com/current",
        {"access_key": get_settings().weatherstack_access_key, "query": city},
    )

    error = response.get("error")
    if error:
        return _degradation_json(f"Weatherstack 查询失败：{error.get('info', '未知错误')}")

    location = response.get("location")
    current = response.get("current")
    if not isinstance(location, dict) or not isinstance(current, dict):
        return _degradation_json("Weatherstack 未返回完整实时天气数据")

    weather_text = (current.get("weather_descriptions") or ["未知天气"])[0]
    temperature = current.get("temperature")
    humidity = current.get("humidity")
    wind_speed = current.get("wind_speed")

    return (
        f"当前天气：{weather_text}，"
        f"温度：{temperature}°C，"
        f"湿度：{humidity}%，"
        f"风速：{wind_speed} km/h。"
    )


@tool(description="获取当前会话绑定的城市名称。未绑定时明确返回未知，不允许编造。")
def get_user_location(runtime: ToolRuntime) -> str:
    """返回当前请求注入的城市，缺失时明确要求用户补充。"""
    city = str(_runtime_context_value(runtime, "city", "")).strip()
    return city if city else "当前会话未绑定城市信息，请让用户明确提供所在城市。"


@tool(description="获取当前会话绑定的用户ID。未绑定时明确返回未知，不允许随机生成。")
def get_user_id(runtime: ToolRuntime):
    """返回当前会话用户标识或说明其缺失。"""
    user_id = _runtime_context_value(runtime, "user_id")
    if user_id:
        return str(user_id)
    return "当前会话未绑定用户ID，请让用户明确提供用户ID。"


@tool(description="获取当前月份，格式为 YYYY-MM。")
def get_current_month():
    """返回当前年月的 YYYY-MM 格式字符串。"""
    return datetime.now().strftime("%Y-%m")


@tool(
    description="获取当前用户的完整健身画像。仅当用户明确要求结合其个人情况、画像、目标、体重、伤病史、训练记录来给建议时调用；通用动作、营养或防护知识问答禁止调用。"
)
def get_user_profile(runtime: ToolRuntime):
    """查询当前用户画像并格式化为 Agent 可使用的信息。"""
    user_id = _runtime_context_value(runtime, "user_id")
    if not user_id:
        return "未获取到用户信息，请让用户先登录。"

    with get_db_session() as db:
        profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        if not profile:
            return "用户尚未填写健身画像，请引导用户完善个人健身信息（年龄、身高、体重、目标等）。"

        injuries = (
            json.loads(profile.injuries) if isinstance(profile.injuries, str) else profile.injuries
        )
        diet_restrict = (
            json.loads(profile.diet_restrict)
            if isinstance(profile.diet_restrict, str)
            else profile.diet_restrict
        )
        preferences = (
            json.loads(profile.preferences)
            if isinstance(profile.preferences, str)
            else profile.preferences
        )

        return (
            f"用户画像：\n"
            f"- 性别：{profile.gender or '未填写'}\n"
            f"- 年龄：{profile.age or '未填写'}\n"
            f"- 身高：{profile.height or '未填写'} cm\n"
            f"- 体重：{profile.weight or '未填写'} kg\n"
            f"- 健身目标：{profile.goal or '未填写'}\n"
            f"- 每周训练天数：{profile.weekly_days} 天\n"
            f"- 运动经验：{profile.experience or '未填写'}\n"
            f"- 伤病史：{', '.join(injuries) if injuries else '无'}\n"
            f"- 饮食限制：{', '.join(diet_restrict) if diet_restrict else '无'}\n"
            f"- 偏好：{preferences or '未填写'}"
        )


@tool(
    description=(
        "按自然语言 query 语义检索当前用户已确认且未过期的长期记忆。由你判断个人目标、"
        "习惯、伤病、饮食或跨会话信息是否有助于回答，再决定是否调用及查询内容；"
        "通用知识问题无需调用，候选和已撤销记忆不可读取。"
    )
)
def get_confirmed_memories(query: str, runtime: ToolRuntime) -> str:
    """只读查询已确认记忆；写入操作始终仅由记忆 API 负责。"""

    user_id = _runtime_context_value(runtime, "user_id")
    if not user_id:
        return "未获取到用户信息，请让用户先登录。"
    return MemoryService().format_relevant_memories(user_id=user_id, query=query)


_MAX_FITNESS_RANGE_DAYS = 90


def _parse_fitness_day(value: str) -> date | None:
    """将紧凑日期字符串解析为日期，格式无效时返回空值。"""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


@tool(
    description=(
        "读取当前用户的受限运动数据摘要。start_day/end_day 均为空时返回近4周汇总；"
        "二者同时传 YYYYMMDD 且不同时，返回该闭区间汇总（最多90天）。"
        "若二者为同一天且用户要分析某次活动，先不传 activity_id 调用一次以获取候选活动；"
        "再把候选中精确的 activity_id 传回，读取该单次活动的时长、距离、心率和负荷等白名单指标。"
        "不得编造活动 ID，不得传入用户 ID。"
    )
)
def get_fitness_summary(
    runtime: ToolRuntime,
    start_day: str = "",
    end_day: str = "",
    activity_id: str = "",
) -> str:
    """从后端注入的当前用户范围内读取默认、区间或单次活动摘要。"""

    user_id = _runtime_context_value(runtime, "user_id")
    if not user_id:
        return "未获取到用户信息，请让用户先登录。"

    start_day = start_day.strip()
    end_day = end_day.strip()
    activity_id = activity_id.strip()
    if bool(start_day) != bool(end_day):
        return "start_day 和 end_day 必须同时提供，格式为 YYYYMMDD。"
    if not start_day and activity_id:
        return "读取单次活动时必须同时提供相同的 start_day 和 end_day。"

    start_date = _parse_fitness_day(start_day)
    end_date = _parse_fitness_day(end_day)
    if start_day and (start_date is None or end_date is None):
        return "日期格式必须是 YYYYMMDD。"
    if start_date and end_date:
        if start_date > end_date:
            return "start_day 不能晚于 end_day。"
        if end_date > date.today():
            return "不能查询未来日期的运动数据。"
        if (end_date - start_date).days + 1 > _MAX_FITNESS_RANGE_DAYS:
            return f"单次查询最多支持 {_MAX_FITNESS_RANGE_DAYS} 天。"
        if activity_id and start_date != end_date:
            return "activity_id 只能用于 start_day 与 end_day 相同的一天。"

    with get_db_session() as db:
        if activity_id:
            activity = load_activity_snapshot(
                db,
                user_id=user_id,
                activity_date=start_date,
                external_id=activity_id,
            )
            if activity is None:
                return "未找到该日期下对应 activity_id 的活动，请先获取当天候选活动后再选择。"
            return activity.to_prompt()

        snapshot = load_fitness_snapshot(
            db,
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
        )
        summary = snapshot.to_prompt()
        if start_date is None or start_date != end_date:
            return summary

        candidates = list_activity_candidates(db, user_id=user_id, activity_date=start_date)
        if not candidates:
            return summary
        candidate_text = "\n".join(candidate.to_prompt() for candidate in candidates)
        return (
            f"{summary}\n\n当天可定位活动如下。若用户指定其中某一次，请使用相同日期和对应 "
            f"activity_id 再调用本工具：\n{candidate_text}"
        )


@tool(
    description="无入参，当识别到用户想生成近期运动总结报告时调用，触发报告模式切换。仅在用户明确要求生成报告/总结时调用。"
)
def trigger_report(runtime: ToolRuntime) -> Command:
    """在本次 Agent 状态中触发报告模式，供下一轮模型切换提示词。"""
    return _tool_state_command(
        "已切换到报告模式，请基于用户近期运动数据生成报告",
        runtime,
        report=True,
    )


if __name__ == "__main__":
    # print(rag_summarize.invoke({"query": "深蹲标准动作"}))
    text = get_weather.run("GuangZhou")
    print(text)
