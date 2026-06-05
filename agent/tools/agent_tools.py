from datetime import datetime
import json
import os
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import URLError
from contextvars import ContextVar
from langchain_core.tools import tool
from rag.rag_service import RagSummarizeService
from utils.logger_handler import logger
from server.database import SessionLocal
from server.models import UserProfile

rag = RagSummarizeService()

_user_context: ContextVar[dict] = ContextVar("user_context", default={})

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

@tool(description="从知识库检索专业资料原始片段。可选通过source指定领域缩小范围：动作指南、营养学、训练计划、损伤预防、基础知识")
def rag_summarize(query: str, source: str = "") -> str:
    source_filter = SOURCE_MAP.get(source) if source else None
    return rag.rag_summarize(query, source_filter)

@tool(description="获取指定城市的实时天气信息，返回温度、体感温度、降水、风速等数据")
def get_weather(city: str):
    city = city.strip()
    if not city:
        return "城市不能为空。"

    try:
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

        weather_data = _request_json(
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,wind_speed_10m,weather_code",
                "timezone": "auto",
            },
        )
        current = weather_data.get("current") or {}
        if not current:
            return f"已定位到 {resolved_name}，但未获取到实时天气数据。"

        weather_code_map = {
            0: "晴", 1: "大部晴朗", 2: "局部多云", 3: "阴",
            45: "雾", 48: "冻雾",
            51: "小毛毛雨", 53: "毛毛雨", 55: "强毛毛雨",
            61: "小雨", 63: "中雨", 65: "大雨",
            71: "小雪", 73: "中雪", 75: "大雪",
            80: "阵雨", 81: "较强阵雨", 82: "强阵雨",
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
    except URLError as e:
        logger.warning(f"天气查询失败：{str(e)}")
        return f"天气服务当前不可用，无法获取 {city} 的实时天气。"
    except Exception as e:
        logger.error(f"天气查询异常：{str(e)}", exc_info=True)
        return f"获取 {city} 天气时发生异常。"


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

@tool(description="获取当前用户的完整健身画像，包括性别、年龄、身高、体重、健身目标、训练经验、伤病史、饮食限制等信息。每位用户首次提问时建议主动调用一次。")
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

        injuries = json.loads(profile.injuries) if isinstance(profile.injuries, str) else profile.injuries
        diet_restrict = json.loads(profile.diet_restrict) if isinstance(profile.diet_restrict, str) else profile.diet_restrict
        preferences = json.loads(profile.preferences) if isinstance(profile.preferences, str) else profile.preferences

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

@tool(description="无入参，当识别到用户想生成近期运动总结报告时调用，触发报告模式切换。仅在用户明确要求生成报告/总结时调用。")
def trigger_report():
    return "trigger_report已调用"

if __name__ == '__main__':
    print(rag_summarize.invoke({"query": "深蹲标准动作"}))
