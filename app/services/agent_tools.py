from datetime import datetime, date, timedelta
import json
import os
import time
import threading
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import URLError
from contextvars import ContextVar
from functools import lru_cache, wraps
from langchain_core.tools import tool
from app.services.rag_service import RagSummarizeService
from app.utils.logger_handler import logger
from app.core.database import SessionLocal
from app.models import UserProfile, FitnessData


@lru_cache(maxsize=1)
def _get_rag_service() -> RagSummarizeService:
    """首次实际检索时再初始化 RAG，避免导入路由时连接外部模型。"""

    return RagSummarizeService()


_user_context: ContextVar[dict] = ContextVar("user_context", default={})


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
        @wraps(func)
        def wrapper(*args, **kwargs):
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
        with self._lock:
            self.failure_count = 0
            self.state = "closed"

    def on_failure(self):
        with self._lock:
            self.failure_count += 1
            # HALF_OPEN 下试探失败，或 CLOSED 下累计到阈值 → OPEN
            if self.state == "half_open" or self.failure_count >= self.failure_threshold:
                self.state = "open"
                self.opened_at = time.time()


def _with_circuit_breaker(name: str, failure_threshold: int = 3, recovery_timeout: float = 30.0):
    """熔断 + 降级层：包在 _with_retry 外面。
    - 熔断 OPEN 时直接返回降级 JSON，不真调（快速失败，给下游喘息）
    - 放行时：调用异常→记失败+降级；成功→记成功。
    每个被装饰函数持有独立熔断器（一个工具挂了不影响别的工具）。"""
    breaker = CircuitBreaker(name, failure_threshold, recovery_timeout)

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
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
def rag_summarize(query: str, source: str = "") -> str:
    source_filter = SOURCE_MAP.get(source) if source else None
    return _get_rag_service().rag_summarize(query, source_filter)


@tool(description="获取指定城市的实时天气信息，返回温度、体感温度、降水、风速等数据")
@_with_circuit_breaker(name="get_weather")
@_with_retry()
def get_weather(city: str):
    city = city.strip()
    if not city:
        return json.dumps(
            {"status": "error", "message": "城市不能为空", "suggestion": "请提供有效的城市名称"},
            ensure_ascii=False,
        )

    # 网络异常由 @_with_retry 兜底重试，这里只处理业务异常
    geocode_data = _request_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        {"name": city, "count": 1, "language": "zh", "format": "json"},
    )
    results = geocode_data.get("results") or []
    if not results:
        return f"未查询到城市 {city} 的地理信息，请确认城市名称。"

    location = results[0]
    latitude = location["latitude"]
    longitude = location["longitude"]
    resolved_name = location.get("name", city)
    admin1 = location.get("admin1", "")
    country = location.get("country", "")

    weather_fields = (
        "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,"
        "wind_speed_10m,weather_code"
    )
    weather_data = _request_json(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": latitude,
            "longitude": longitude,
            "current": weather_fields,
            "timezone": "auto",
        },
    )

    current = weather_data.get("current") or {}
    if not current:
        return f"已定位到 {resolved_name}，但未获取到实时天气数据。"

    weather_code_map = {
        0: "晴",
        1: "大部晴朗",
        2: "局部多云",
        3: "阴",
        45: "雾",
        48: "冻雾",
        51: "小毛毛雨",
        53: "毛毛雨",
        55: "强毛毛雨",
        61: "小雨",
        63: "中雨",
        65: "大雨",
        71: "小雪",
        73: "中雪",
        75: "大雪",
        80: "阵雨",
        81: "较强阵雨",
        82: "强阵雨",
        95: "雷暴",
    }
    weather_text = weather_code_map.get(current.get("weather_code"), "未知天气")

    location_text = ", ".join(filter(None, [resolved_name, admin1, country]))
    return (
        f"{location_text} 当前天气：{weather_text}；"
        f"温度：{current.get('temperature_2m')}°C，"
        f"体感温度：{current.get('apparent_temperature')}°C，"
        f"相对湿度：{current.get('relative_humidity_2m')}%，"
        f"降水：{current.get('precipitation')} mm，"
        f"风速：{current.get('wind_speed_10m')} km/h。"
    )


@tool(description="获取当前会话绑定的城市名称。未绑定时明确返回未知，不允许编造。")
def get_user_location() -> str:
    ctx = _user_context.get()
    if ctx.get("city"):
        return ctx["city"]
    city = os.getenv("AGENT_USER_CITY", "").strip()
    return city if city else "当前会话未绑定城市信息，请让用户明确提供所在城市。"


@tool(description="获取当前会话绑定的用户ID。未绑定时明确返回未知，不允许随机生成。")
def get_user_id():
    ctx = _user_context.get()
    if ctx.get("user_id"):
        return str(ctx["user_id"])
    return "当前会话未绑定用户ID，请让用户明确提供用户ID。"


@tool(description="获取当前月份，格式为 YYYY-MM。")
def get_current_month():
    return datetime.now().strftime("%Y-%m")


@tool(
    description="获取当前用户的完整健身画像，包括性别、年龄、身高、体重、健身目标、训练经验、伤病史、饮食限制等信息。每位用户首次提问时建议主动调用一次。"
)
def get_user_profile():
    ctx = _user_context.get()
    user_id = ctx.get("user_id")
    if not user_id:
        return "未获取到用户信息，请让用户先登录。"

    db = SessionLocal()
    try:
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
    finally:
        db.close()


@tool(
    description="获取用户近4周运动数据摘要：含平均静息心率、HRV、训练负荷、睡眠时长/质量、运动类型分布。当用户询问训练建议/运动报告/身体状态时调用，与用户画像和天气数据一起为个性化推荐提供数据基础。"
)
def get_fitness_summary() -> str:
    # 从请求级 ContextVar 取 user_id（chat 端点会在调用 Agent 前设置）
    ctx = _user_context.get()
    user_id = ctx.get("user_id")
    if not user_id:
        return "未获取到用户信息，请让用户先登录。"

    # 手动开 DB 会话：工具函数在 LangChain Agent 内部调用，不走 FastAPI Depends 注入
    db = SessionLocal()
    try:
        # 查近4周所有健身数据
        since = date.today() - timedelta(weeks=4)
        records = (
            db.query(FitnessData)
            .filter(FitnessData.user_id == user_id, FitnessData.date >= since)
            .all()
        )

        if not records:
            return (
                "用户近4周暂无运动数据。请引导用户去 Dashboard 点击「同步」按钮获取"
                "高驰设备数据，同步后即可基于真实运动数据提供个性化建议。"
            )

        # 按 data_type 分三类，同时解析 JSON 字符串为 dict
        daily_list, sleep_list, activities = [], [], []
        for r in records:
            parsed = json.loads(r.data) if isinstance(r.data, str) else r.data
            if r.data_type == "daily_metrics":
                daily_list.append(parsed)
            elif r.data_type == "sleep":
                sleep_list.append(parsed)
            else:
                activities.append(parsed)

        lines = ["用户近4周运动数据摘要："]

        # === 日指标聚合：恢复(rhr+hrv) + 负荷(tl+ratio) + 状态(tired) + 能力(vo2max) ===
        if daily_list:
            # filter 用 .get() 排掉缺字段的记录，值用 [] 直接取（filter 已保证 key 存在）
            rhr_vals = [d["rhr"] for d in daily_list if d.get("rhr")]
            hrv_vals = [d["avg_sleep_hrv"] for d in daily_list if d.get("avg_sleep_hrv")]
            tl_vals = [d["training_load"] for d in daily_list if d.get("training_load")]
            ratio_vals = [
                d["training_load_ratio"] for d in daily_list if d.get("training_load_ratio")
            ]
            tired_vals = [d["tired_rate"] for d in daily_list if d.get("tired_rate")]
            vo2_vals = [d["vo2max"] for d in daily_list if d.get("vo2max")]

            lines.append(f"- 有效日指标数据：{len(daily_list)}天")
            if rhr_vals:
                avg_rhr = sum(rhr_vals) / len(rhr_vals)
                # 静息心率偏低=恢复好、偏高=疲劳；给 LLM 一个可直接引用的中文状态标签
                label = (
                    "偏低，恢复良好"
                    if avg_rhr < 60
                    else ("偏高，注意恢复" if avg_rhr > 75 else "正常")
                )
                lines.append(f"- 平均静息心率：{avg_rhr:.0f} bpm（{label}）")
            if hrv_vals:
                lines.append(f"- 平均睡眠HRV(RMSSD)：{sum(hrv_vals) / len(hrv_vals):.0f} ms")
            if tl_vals:
                lines.append(
                    f"- 训练负荷：日均{sum(tl_vals) / len(tl_vals):.0f}，单日最高{max(tl_vals)}"
                )
            if ratio_vals:
                avg_ratio = sum(ratio_vals) / len(ratio_vals)
                # 急/慢性比 > 1.3 通常表示短期负荷远超长期平均，overtraining 风险信号
                ratio_label = "急性负荷偏高，注意恢复" if avg_ratio > 1.3 else "负荷比例正常"
                lines.append(f"- 训练负荷比(急性/慢性)：{avg_ratio:.1f}（{ratio_label}）")
            if tired_vals:
                lines.append(f"- 平均疲劳度：{sum(tired_vals) / len(tired_vals):.1f}")
            if vo2_vals:
                # VO2max 取最新值（非平均），反映当前心肺能力
                lines.append(f"- 最新VO2max：{vo2_vals[-1]}")

        # === 睡眠聚合：总时长 + 深度睡眠（恢复质量的核心指标）===
        if sleep_list:
            durations = [
                s["total_duration_minutes"] for s in sleep_list if s.get("total_duration_minutes")
            ]
            # phases 是嵌套 dict，用海象运算符 := 避免重复取值
            deep_vals = []
            for s in sleep_list:
                if (phases := s.get("phases")) and phases.get("deep_minutes"):
                    deep_vals.append(phases["deep_minutes"])
            if durations:
                average_hours = sum(durations) / len(durations) / 60
                lines.append(f"- 平均睡眠时长：{average_hours:.1f}小时（{len(durations)}天）")
            if deep_vals:
                lines.append(f"- 平均深度睡眠：{sum(deep_vals) / len(deep_vals):.0f}分钟")

        # === 运动聚合：次数 + 类型分布 + 总时长 ===
        if activities:
            sport_counts = {}
            total_sec = 0
            for a in activities:
                # name 是用户在手表中看到的运动中文名（如"跑步""骑行"），比 sport_name 更友好
                sport = a.get("name", "未知运动")
                sport_counts[sport] = sport_counts.get(sport, 0) + 1
                if a.get("duration_seconds"):
                    total_sec += a["duration_seconds"]
            lines.append(f"- 运动次数：{len(activities)}次")
            # 按出现次数降序取前5种运动类型，避免长列表塞爆输出
            top_sports = sorted(sport_counts.items(), key=lambda x: -x[-1])[:5]
            lines.append(f"- 运动类型分布：{'、'.join(f'{k}x{v}' for k, v in top_sports)}")
            if total_sec:
                lines.append(f"- 总运动时长：{total_sec / 3600:.1f}小时")

        return "\n".join(lines)
    finally:
        db.close()


@tool(
    description="无入参，当识别到用户想生成近期运动总结报告时调用，触发报告模式切换。仅在用户明确要求生成报告/总结时调用。"
)
def trigger_report():
    """信号工具：调用后中间件（middleware.py 的 monitor_tool）会把
    request.runtime.context['report'] 置为 True，下一轮 LLM 调用
    通过 report_prompt_switch 切到 report_prompt。
    本工具不返回数据，仅触发 prompt 切换。"""
    return "已切换到报告模式，请基于用户近期运动数据生成报告"


if __name__ == "__main__":
    print(rag_summarize.invoke({"query": "深蹲标准动作"}))
