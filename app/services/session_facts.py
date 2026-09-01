"""从会话历史提取轻量且非敏感的事实。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


COMMON_CITIES = (
    "北京",
    "上海",
    "广州",
    "深圳",
    "杭州",
    "苏州",
    "南京",
    "成都",
    "重庆",
    "天津",
    "武汉",
    "西安",
    "长沙",
    "青岛",
    "宁波",
    "厦门",
    "郑州",
    "合肥",
    "福州",
    "济南",
)

INVALID_CITY_VALUES = {"哪个城市", "什么城市", "哪座城市", "哪个市", "哪里", "哪儿"}

GOAL_KEYWORDS = {
    "增肌": ("增肌", "长肌肉", "变大", "变壮", "增重"),
    "减脂": ("减脂", "减肥", "减重", "瘦身", "瘦下来", "掉秤"),
    "塑形": ("塑形", "塑型", "线条", "紧致", "马甲线", "腹肌"),
    "耐力": ("耐力", "体能", "心肺", "有氧"),
}

INJURY_KEYWORDS = {
    "膝盖伤": ("膝盖", "膝关节"),
    "腰伤": ("腰", "腰椎", "腰间盘"),
    "肩伤": ("肩", "肩关节", "肩袖"),
    "手腕伤": ("手腕", "腕关节"),
    "踝伤": ("脚踝", "踝关节", "崴脚"),
    "颈椎": ("颈椎", "脖子"),
}

DIET_KEYWORDS = {
    "素食": ("素食", "吃素", "不吃肉"),
    "低碳水": ("低碳水", "低碳", "生酮", "戒碳水"),
    "高蛋白": ("高蛋白", "多吃肉", "多吃蛋"),
}

CITY_PATTERN = re.compile(
    r"(?:在|住在|来自|位于)([^\s，。！？,.!?]{2,12}(?:市|"
    r"县|区|北京|上海|广州|深圳|杭州|苏州|南京|"
    r"成都|重庆|天津|武汉|西安|长沙|青岛|宁波|厦门"
    r"|郑州|合肥|福州|济南))"
)


def _first_matching_label(content: str, rules: dict[str, tuple[str, ...]]) -> str | None:
    """返回规则中首个在文本出现关键词的规范标签。"""
    return next(
        (
            label
            for label, keywords in rules.items()
            if any(keyword in content for keyword in keywords)
        ),
        None,
    )


def extract_session_facts(messages: Iterable[dict[str, Any]]) -> dict[str, str | list[str]]:
    """提取供路由和提示词构建使用的确定性会话事实。"""

    facts: dict[str, str | list[str]] = {}
    injuries: list[str] = []
    for message in messages:
        # 只信任用户的原始表达；不能把模型回答或工具输出反向写入会话事实。
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue

        city = next((item for item in reversed(COMMON_CITIES) if item in content), None)
        city_match = CITY_PATTERN.search(content)
        if city_match and city_match.group(1) not in INVALID_CITY_VALUES:
            city = city_match.group(1)
        if city:
            facts["city"] = city

        goal = _first_matching_label(content, GOAL_KEYWORDS)
        if goal:
            facts["training_goal"] = goal

        for injury, keywords in INJURY_KEYWORDS.items():
            if any(keyword in content for keyword in keywords) and injury not in injuries:
                injuries.append(injury)

        diet = _first_matching_label(content, DIET_KEYWORDS)
        if diet:
            facts["diet_pref"] = diet

    if injuries:
        facts["injuries"] = injuries
    return facts
