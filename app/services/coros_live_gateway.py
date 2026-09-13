"""通过官方远程 MCP 按需读取并最小化 COROS 运动数据。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from typing import Any, Awaitable, Callable, Iterable

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from sqlalchemy.orm import Session as DBSession

from app.core.settings import Settings, get_settings
from app.services.coros_oauth import (
    CorosAccessCredential,
    CorosOAuthService,
    CorosReconnectionRequiredError,
)


# 由应用生命周期配置独立文件处理器，避免导入模块时写入测试日志。
logger = logging.getLogger("coros_live")


class CorosMcpError(RuntimeError):
    """官方 MCP 的安全业务错误，不携带上游响应正文。"""


class CorosMcpSchemaError(CorosMcpError):
    """官方工具目录与已审核的白名单契约不兼容。"""


class CorosMcpUnavailableError(CorosMcpError):
    """所有所需运动数据源均无法返回。"""


class CorosMcpUnauthorizedError(CorosMcpError):
    """远程 MCP 拒绝了当前 access token。"""


@dataclass(frozen=True)
class LiveFitnessData:
    """仅在单次请求内存在的标准化 COROS 数据。"""

    start_date: date
    end_date: date
    daily_metrics: list[dict[str, Any]] = field(default_factory=list)
    sleep_records: list[dict[str, Any]] = field(default_factory=list)
    activities: list[dict[str, Any]] = field(default_factory=list)
    unavailable_sources: list[str] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        return bool(self.unavailable_sources)


class CorosLiveGateway:
    """隐藏 OAuth、MCP 与字段兼容性，仅暴露实时运动快照接口。"""

    _SERVER_NAME = "coros"
    _TOOL_NAMES = {
        "activities": "querySportRecords",
        "daily": "queryDailyHealthData",
        "sleep": "querySleepData",
        "training_load": "queryTrainingLoadAssessment",
        "resting_heart_rate": "queryRestingHeartRate",
        "sleep_hrv": "querySleepHrv",
        "activity_detail": "getActivityDetail",
    }
    _CORE_SOURCES = ("activities", "daily", "sleep")
    _ENRICHMENT_SOURCES = ("training_load", "resting_heart_rate", "sleep_hrv")
    _DATE_START_KEYS = ("startDate", "start_date", "startDay", "start_day", "fromDate")
    _DATE_END_KEYS = ("endDate", "end_date", "endDay", "end_day", "toDate")
    _ACTIVITY_ID_KEYS = ("activityId", "activity_id", "sportRecordId", "recordId", "id")
    _PAGE_SIZE_KEYS = ("pageSize", "page_size", "limit")
    _PAGE_NUMBER_KEYS = ("page", "pageNumber", "page_number", "pageNum", "page_num")
    _CURSOR_KEYS = ("cursor", "pageCursor", "page_cursor")
    _NEXT_CURSOR_KEYS = ("nextCursor", "next_cursor", "nextPageCursor", "next_page_cursor")
    _HAS_MORE_KEYS = ("hasMore", "has_more", "more")
    _MAX_PAGES = 20
    _MAX_SPECIALIST_DAYS = 7
    _TEXT_ERROR_MARKERS = (
        "tool call anomalies detected",
        "session context pollution",
        "request exceeds the llm capability boundary",
    )
    _ACTIVITY_FILTERS = {
        "sportTypeCodes": [65535],
        "minDistanceKm": 0,
        "maxDistanceKm": 10000,
        "minDurationMinutes": 0,
        "maxDurationMinutes": 1440,
        "maxAveragePace": "",
        "locationKeyword": "",
    }

    def __init__(
        self,
        *,
        oauth_service: CorosOAuthService | None = None,
        settings: Settings | None = None,
        client_factory: Callable[..., MultiServerMCPClient] = MultiServerMCPClient,
        tool_loader: Callable[..., Awaitable[list[Any]]] = load_mcp_tools,
    ) -> None:
        self._settings = settings or get_settings()
        self._oauth = oauth_service or CorosOAuthService(settings=self._settings)
        self._client_factory = client_factory
        self._tool_loader = tool_loader

    @staticmethod
    def _tool_fields(tool: Any) -> set[str]:
        schema = getattr(tool, "args_schema", None)
        fields = getattr(schema, "model_fields", None)
        if isinstance(fields, dict):
            return set(fields)
        if isinstance(schema, dict):
            properties = schema.get("properties", {})
            if isinstance(properties, dict):
                return set(properties)
        return set()

    @classmethod
    def _find_field(cls, fields: set[str], candidates: Iterable[str]) -> str | None:
        return next((candidate for candidate in candidates if candidate in fields), None)

    @classmethod
    def _date_arguments(cls, tool: Any, start_date: date, end_date: date) -> dict[str, str]:
        """按当前 COROS 文本工具契约生成紧凑日期参数。"""

        fields = cls._tool_fields(tool)
        start_key = cls._find_field(fields, cls._DATE_START_KEYS)
        end_key = cls._find_field(fields, cls._DATE_END_KEYS)
        if start_key is None and end_key is None:
            raise CorosMcpSchemaError("COROS 工具未声明可验证的日期范围参数")
        if start_key is None or end_key is None:
            raise CorosMcpSchemaError("COROS 工具日期参数不完整，暂无法安全调用")
        return {
            start_key: start_date.strftime("%Y%m%d"),
            end_key: end_date.strftime("%Y%m%d"),
        }

    @classmethod
    def _days_arguments(cls, tool: Any, start_date: date, end_date: date) -> dict[str, int]:
        """只在 schema 声明 days 时传入包含首尾日期的安全天数。"""

        if "days" not in cls._tool_fields(tool):
            return {}
        days = (end_date - start_date).days + 1
        return {"days": max(1, min(days, cls._MAX_SPECIALIST_DAYS))}

    @classmethod
    def _source_arguments(
        cls, source: str, tool: Any, start_date: date, end_date: date
    ) -> dict[str, Any]:
        """依赖运行时 schema 构造各读取工具的最小完整参数。"""

        fields = cls._tool_fields(tool)
        if source in {"daily", "training_load", "resting_heart_rate"}:
            return cls._days_arguments(tool, start_date, end_date)

        arguments: dict[str, Any] = cls._date_arguments(tool, start_date, end_date)
        if source in {"sleep", "sleep_hrv"}:
            arguments.update(cls._days_arguments(tool, start_date, end_date))
        if source == "activities":
            arguments.update(
                {name: value for name, value in cls._ACTIVITY_FILTERS.items() if name in fields}
            )
        return arguments

    @classmethod
    def _activity_arguments(cls, tool: Any, activity_id: str) -> dict[str, str]:
        key = cls._find_field(cls._tool_fields(tool), cls._ACTIVITY_ID_KEYS)
        if key is None:
            raise CorosMcpSchemaError("COROS 活动详情工具参数不兼容")
        return {key: activity_id}

    @staticmethod
    def _is_unauthorized(error: BaseException) -> bool:
        current: BaseException | None = error
        while current is not None:
            response = getattr(current, "response", None)
            if getattr(response, "status_code", None) == 401:
                return True
            if getattr(current, "status_code", None) == 401:
                return True
            current = current.__cause__
        return False

    @staticmethod
    def _error_status_code(error: BaseException) -> int | None:
        """提取上游状态码，不记录可能包含敏感信息的响应正文。"""

        response = getattr(error, "response", None)
        value = getattr(response, "status_code", getattr(error, "status_code", None))
        return value if isinstance(value, int) else None

    @staticmethod
    def _decode_result(value: Any) -> Any:
        """兼容 LangChain ToolMessage、文本 JSON 与 JSON 原生返回。"""

        if isinstance(value, tuple):
            value = value[0]
        content = getattr(value, "content", value)
        if isinstance(content, list):
            text_parts = [
                item.get("text", "") if isinstance(item, dict) else str(item) for item in content
            ]
            content = "".join(text_parts)
        if isinstance(content, str):
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return content
        return content

    @staticmethod
    def _number(text: str, pattern: str) -> int | float | None:
        """从已脱敏的局部文本提取首个数值，缺失时保持为空。"""

        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            return None
        value = float(match.group(1).replace(",", ""))
        return int(value) if value.is_integer() else value

    @staticmethod
    def _duration_seconds(text: str) -> int | None:
        """将 COROS 的时分秒文本转换为秒数，不推断缺失时间。"""

        match = re.search(r"Duration:\s*(\d{1,2}):(\d{2})(?::(\d{2}))?", text, re.I)
        if match is None:
            return None
        first, minute, second = (int(value or 0) for value in match.groups())
        return (first * 3600 + minute * 60 + second) if match.group(3) else (first * 60 + minute)

    @classmethod
    def _empty_daily_metric(cls, record_date: str) -> dict[str, Any]:
        """生成兼容既有快照字段的每日指标基线。"""

        return {
            "date": record_date,
            "rhr": None,
            "avg_sleep_hrv": None,
            "training_load": None,
            "training_load_ratio": None,
            "tired_rate": None,
            "vo2max": None,
        }

    @classmethod
    def _parse_activity_text(cls, text: str) -> list[dict[str, Any]]:
        """解析活动摘要，只保留面板和训练所需的非位置字段。"""

        records: list[dict[str, Any]] = []
        pattern = re.compile(
            r"(?ms)^\s*\d+\.\s*(?P<name>.+?)\s+[—-]\s*(?P<date>\d{4}-\d{2}-\d{2})"
            r"(?P<body>.*?)(?=^\s*\d+\.\s+|\Z)"
        )
        for match in pattern.finditer(text):
            body = match.group("body")
            activity_id = re.search(r"LabelId:\s*([^\s|]+)", body, re.I)
            if activity_id is None:
                continue
            distance_km = cls._number(body, r"Distance:\s*([\d.,]+)\s*km")
            records.append(
                {
                    "external_id": activity_id.group(1),
                    "date": match.group("date"),
                    "start_time": "",
                    "name": match.group("name").strip(),
                    "sport_name": match.group("name").strip(),
                    "duration_seconds": cls._duration_seconds(body),
                    "distance_meters": int(distance_km * 1000) if distance_km is not None else None,
                    "avg_heart_rate": cls._number(body, r"Avg HR:\s*([\d.,]+)\s*bpm"),
                    "max_heart_rate": None,
                    "training_load": None,
                    "calories": cls._number(body, r"Calories:\s*([\d.,]+)\s*kcal"),
                }
            )
        return records

    @classmethod
    def _parse_daily_text(cls, text: str) -> list[dict[str, Any]]:
        """解析每日健康文本，并把全局 HR/HRV 摘要附到最新日期。"""

        records: list[dict[str, Any]] = []
        pattern = re.compile(
            r"(?ms)^---\s*(?P<date>\d{8})\s*---\s*(?P<body>.*?)(?=^---\s*\d{8}\s*---|\Z)"
        )
        for match in pattern.finditer(text):
            record = cls._empty_daily_metric(cls._date_value(match.group("date")))
            body = match.group("body")
            optional_values = {
                "steps": cls._number(body, r"Steps:\s*([\d.,]+)"),
                "calories": cls._number(body, r"Calories:\s*([\d.,]+)\s*kcal"),
                "exercise_minutes": cls._number(body, r"Exercise:\s*([\d.,]+)\s*min"),
            }
            record.update(
                {key: value for key, value in optional_values.items() if value is not None}
            )
            records.append(record)
        if records:
            records[-1].update(
                {
                    key: value
                    for key, value in {
                        "rhr": cls._number(text, r"Resting HR:\s*([\d.,]+)\s*bpm"),
                        "avg_sleep_hrv": cls._number(text, r"HRV Baseline:\s*([\d.,]+)\s*ms"),
                    }.items()
                    if value is not None
                }
            )
        return records

    @classmethod
    def _parse_sleep_text(cls, text: str) -> list[dict[str, Any]]:
        """解析睡眠与午睡分钟数；缺少阶段时保留明确的空阶段记录。"""

        records: list[dict[str, Any]] = []
        pattern = re.compile(
            r"(?ms)^\s*(?P<date>\d{4}-\d{2}-\d{2})\s*:?\s*(?P<body>.*?)(?=^\s*\d{4}-\d{2}-\d{2}\s*:|\Z)"
        )
        phase_patterns = {
            "awake_minutes": r"Awake(?:\s+Sleep)?\s*:\s*([\d.,]+)\s*min",
            "rem_minutes": r"REM(?:\s+Sleep)?\s*:\s*([\d.,]+)\s*min",
            "light_minutes": r"Light(?:\s+Sleep)?\s*:\s*([\d.,]+)\s*min",
            "deep_minutes": r"Deep(?:\s+Sleep)?\s*:\s*([\d.,]+)\s*min",
        }
        for match in pattern.finditer(text):
            body = match.group("body")
            if cls._is_no_data_text(body):
                continue
            phases = {
                key: value
                for key, value in (
                    (key, cls._number(body, expression))
                    for key, expression in phase_patterns.items()
                )
                if value is not None
            }
            record: dict[str, Any] = {
                "date": match.group("date"),
                "total_duration_minutes": cls._number(
                    body, r"(?:Total Sleep|Sleep Duration):\s*([\d.,]+)\s*min"
                ),
                "phases": phases,
            }
            nap_minutes = cls._number(body, r"Naps? Total:\s*([\d.,]+)\s*min")
            if nap_minutes is not None:
                record["nap_minutes"] = nap_minutes
            records.append(record)
        return records

    @classmethod
    def _parse_training_load_text(cls, text: str) -> list[dict[str, Any]]:
        """解析每日短期负荷和负荷比，避免把描述文本暴露给客户端。"""

        records: list[dict[str, Any]] = []
        pattern = re.compile(
            r"(?ms)^\s*(?P<date>\d{4}-\d{2}-\d{2})\s*(?P<body>.*?)(?=^\s*\d{4}-\d{2}-\d{2}\s*|\Z)"
        )
        for match in pattern.finditer(text):
            load = cls._number(match.group("body"), r"Short-Term Load:\s*([\d.,]+)")
            ratio = cls._number(match.group("body"), r"Load Ratio:\s*([\d.,]+)")
            if load is None and ratio is None:
                continue
            record = cls._empty_daily_metric(match.group("date"))
            metrics = {"training_load": load, "training_load_ratio": ratio}
            record.update({key: value for key, value in metrics.items() if value is not None})
            records.append(record)
        return records

    @classmethod
    def _parse_time_series_text(cls, text: str, metric: str) -> list[dict[str, Any]]:
        """解析按日期返回的静息心率或睡眠 HRV 时序。"""

        unit = "bpm" if metric == "rhr" else "ms"
        records: list[dict[str, Any]] = []
        for match in re.finditer(
            rf"(?m)^\s*(\d{{4}}-\d{{2}}-\d{{2}})\s*:\s*(?:[^\n]*?\s*)?([\d.,]+)\s*{unit}\b",
            text,
            re.I,
        ):
            record = cls._empty_daily_metric(match.group(1))
            record[metric] = cls._number(match.group(2), r"([\d.,]+)")
            records.append(record)
        return records

    @classmethod
    def _is_no_data_text(cls, text: str) -> bool:
        """识别 MCP 明确的无数据响应，避免把它当作调用失败。"""

        normalized = text.lower()
        return (
            "no data" in normalized
            or "no records" in normalized
            or "no " in normalized
            and " found" in normalized
        )

    @classmethod
    def _parse_text_source(cls, source: str, text: str) -> list[dict[str, Any]]:
        """将已知 COROS 文本协议转换为记录，未知非空文本显式失败。"""

        normalized = text.strip()
        if not normalized or cls._is_no_data_text(normalized):
            return []
        if any(marker in normalized.lower() for marker in cls._TEXT_ERROR_MARKERS):
            logger.warning("COROS_MCP_SOURCE_TEXT_FAILED source=%s text_kind=tool_error", source)
            raise CorosMcpError("COROS MCP 数据读取失败，请稍后重试")
        parsers: dict[str, Callable[[str], list[dict[str, Any]]]] = {
            "activities": cls._parse_activity_text,
            "daily": cls._parse_daily_text,
            "sleep": cls._parse_sleep_text,
            "training_load": cls._parse_training_load_text,
            "resting_heart_rate": lambda value: cls._parse_time_series_text(value, "rhr"),
            "sleep_hrv": lambda value: cls._parse_time_series_text(value, "avg_sleep_hrv"),
        }
        records = parsers[source](normalized)
        if records:
            return records
        logger.warning("COROS_MCP_SOURCE_TEXT_FAILED source=%s text_kind=unrecognized", source)
        raise CorosMcpSchemaError("COROS MCP 返回的数据格式不兼容")

    @staticmethod
    def _records(payload: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        nested = payload.get("data")
        if isinstance(nested, dict):
            return CorosLiveGateway._records(nested, keys)
        return []

    @staticmethod
    def _pagination_metadata(payload: Any) -> tuple[bool, Any | None]:
        """读取常见分页信号；未知但明确未结束的结果不能被当成完整快照。"""

        mappings: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            mappings.append(payload)
            nested = payload.get("data")
            if isinstance(nested, dict):
                mappings.append(nested)
        for mapping in mappings:
            has_more = next(
                (mapping[key] for key in CorosLiveGateway._HAS_MORE_KEYS if key in mapping),
                None,
            )
            next_cursor = next(
                (mapping[key] for key in CorosLiveGateway._NEXT_CURSOR_KEYS if key in mapping),
                None,
            )
            if has_more is not None:
                if isinstance(has_more, str):
                    return has_more.lower() in {"true", "1", "yes"}, next_cursor
                return bool(has_more), next_cursor
            if next_cursor not in (None, ""):
                return True, next_cursor
        return False, None

    @staticmethod
    def _value(record: dict[str, Any], *keys: str) -> Any:
        return next((record[key] for key in keys if record.get(key) not in (None, "")), None)

    @staticmethod
    def _date_value(value: Any) -> str:
        if isinstance(value, datetime):
            return value.date().isoformat()
        text = str(value or "")
        digits = "".join(char for char in text if char.isdigit())
        if len(digits) >= 8:
            return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        return text[:10]

    @classmethod
    def _normalize_daily(cls, record: dict[str, Any]) -> dict[str, Any] | None:
        record_date = cls._date_value(cls._value(record, "date", "day", "recordDate"))
        if not record_date:
            return None
        normalized = cls._empty_daily_metric(record_date)
        normalized.update(
            {
                "rhr": cls._value(record, "rhr", "restingHeartRate"),
                "avg_sleep_hrv": cls._value(record, "avg_sleep_hrv", "sleepHrv", "avgSleepHrv"),
                "training_load": cls._value(record, "training_load", "trainingLoad"),
                "training_load_ratio": cls._value(
                    record, "training_load_ratio", "trainingLoadRatio"
                ),
                "tired_rate": cls._value(record, "tired_rate", "tiredRate", "fatigue"),
                "vo2max": cls._value(record, "vo2max", "vo2Max"),
            }
        )
        optional_values = {
            "steps": cls._value(record, "steps", "stepCount"),
            "calories": cls._value(record, "calories", "calorie", "caloriesBurned"),
            "exercise_minutes": cls._value(record, "exercise_minutes", "exerciseMinutes"),
        }
        normalized.update(
            {key: value for key, value in optional_values.items() if value is not None}
        )
        return normalized

    @classmethod
    def _normalize_sleep(cls, record: dict[str, Any]) -> dict[str, Any] | None:
        record_date = cls._date_value(cls._value(record, "date", "day", "recordDate"))
        if not record_date:
            return None
        raw_phases = cls._value(record, "phases", "sleepStages", "stages")
        phases = raw_phases if isinstance(raw_phases, dict) else {}
        normalized = {
            "date": record_date,
            "total_duration_minutes": cls._value(
                record, "total_duration_minutes", "totalSleepMinutes", "sleepMinutes"
            ),
            "phases": {
                "awake_minutes": cls._value(phases, "awake_minutes", "awakeMinutes") or 0,
                "rem_minutes": cls._value(phases, "rem_minutes", "remMinutes") or 0,
                "light_minutes": cls._value(phases, "light_minutes", "lightMinutes") or 0,
                "deep_minutes": cls._value(phases, "deep_minutes", "deepMinutes") or 0,
            },
        }
        nap_minutes = cls._value(record, "nap_minutes", "napMinutes")
        if nap_minutes is not None:
            normalized["nap_minutes"] = nap_minutes
        return normalized

    @classmethod
    def _normalize_activity(cls, record: dict[str, Any]) -> dict[str, Any] | None:
        external_id = cls._value(
            record, "external_id", "activityId", "activity_id", "id", "recordId", "uuid"
        )
        start_time = cls._value(record, "start_time", "startTime", "startAt")
        record_date = cls._date_value(start_time or cls._value(record, "date", "day"))
        if not external_id or not record_date:
            return None
        return {
            "external_id": str(external_id),
            "date": record_date,
            "start_time": str(start_time or ""),
            "name": str(cls._value(record, "name", "sportName", "sport_name") or "未知运动"),
            "sport_name": str(cls._value(record, "sport_name", "sportName", "name") or "未知运动"),
            "duration_seconds": cls._value(
                record, "duration_seconds", "durationSeconds", "duration"
            ),
            "distance_meters": cls._value(record, "distance_meters", "distanceMeters", "distance"),
            "avg_heart_rate": cls._value(
                record, "avg_heart_rate", "avgHeartRate", "averageHeartRate"
            ),
            "max_heart_rate": cls._value(
                record, "max_heart_rate", "maxHeartRate", "maximumHeartRate"
            ),
            "training_load": cls._value(record, "training_load", "trainingLoad"),
            "calories": cls._value(record, "calories", "calorie", "caloriesBurned"),
        }

    @classmethod
    def _normalize_many(
        cls,
        records: list[dict[str, Any]],
        normalizer: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> list[dict[str, Any]]:
        return [normalized for record in records if (normalized := normalizer(record)) is not None]

    @classmethod
    def _merge_daily_metrics(
        cls, source_records: Iterable[list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        """按日期合并每日健康与专项指标，后者只覆盖实际返回的字段。"""

        merged: dict[str, dict[str, Any]] = {}
        for records in source_records:
            for record in cls._normalize_many(records, cls._normalize_daily):
                target = merged.setdefault(record["date"], cls._empty_daily_metric(record["date"]))
                target.update({key: value for key, value in record.items() if value is not None})
        return list(merged.values())

    @staticmethod
    def _within_date_range(record: dict[str, Any], start_date: date, end_date: date) -> bool:
        """丢弃上游超出请求范围的记录，确保面板日期窗口精确。"""

        record_date = CorosLiveGateway._date_value(record.get("date"))
        return start_date.isoformat() <= record_date <= end_date.isoformat()

    async def _with_tools(
        self,
        credential: CorosAccessCredential,
        operation: Callable[[dict[str, Any]], Awaitable[Any]],
    ) -> Any:
        """在一个 MCP session 内发现白名单工具并完成所有调用。"""

        client = self._client_factory(
            {
                self._SERVER_NAME: {
                    "transport": "streamable_http",
                    "url": f"{credential.issuer.rstrip('/')}/mcp",
                    "headers": {"Authorization": f"Bearer {credential.access_token}"},
                    "timeout": self._settings.coros_mcp_timeout_seconds,
                }
            }
        )
        try:
            async with client.session(self._SERVER_NAME) as session:
                tools = await self._tool_loader(session, server_name=self._SERVER_NAME)
                return await operation({str(tool.name): tool for tool in tools})
        except CorosMcpError:
            raise
        except Exception as error:
            if self._is_unauthorized(error):
                raise CorosMcpUnauthorizedError("COROS MCP 授权已失效") from error
            raise CorosMcpError("COROS MCP 连接失败，请稍后重试") from error

    @staticmethod
    async def _call_tool(tool: Any, arguments: dict[str, Any]) -> Any:
        try:
            result = await tool.ainvoke(arguments)
        except Exception as error:
            logger.warning(
                "COROS_MCP_TOOL_CALL_FAILED tool=%s error_type=%s status_code=%s",
                getattr(tool, "name", "unknown"),
                type(error).__name__,
                CorosLiveGateway._error_status_code(error),
            )
            if CorosLiveGateway._is_unauthorized(error):
                raise CorosMcpUnauthorizedError("COROS MCP 授权已失效") from error
            raise CorosMcpError("COROS MCP 数据读取失败，请稍后重试") from error
        if getattr(result, "status", None) == "error":
            logger.warning(
                "COROS_MCP_TOOL_RESULT_ERROR tool=%s",
                getattr(tool, "name", "unknown"),
            )
            raise CorosMcpError("COROS MCP 数据读取失败，请稍后重试")
        return result

    async def _read_all_pages(
        self, source: str, tool: Any, start_date: date, end_date: date
    ) -> list[dict[str, Any]]:
        """读取文本或有限 JSON 分页；文本结果在首次调用即转为安全记录。"""

        fields = self._tool_fields(tool)
        arguments: dict[str, Any] = self._source_arguments(source, tool, start_date, end_date)
        page_size_key = self._find_field(fields, self._PAGE_SIZE_KEYS)
        page_number_key = self._find_field(fields, self._PAGE_NUMBER_KEYS)
        cursor_key = self._find_field(fields, self._CURSOR_KEYS)
        if page_size_key is not None:
            arguments[page_size_key] = 100
        if page_number_key is not None:
            arguments[page_number_key] = 1

        records: list[dict[str, Any]] = []
        for page_index in range(self._MAX_PAGES):
            result = await self._call_tool(tool, arguments)
            payload = self._decode_result(result)
            if isinstance(payload, str):
                return self._parse_text_source(source, payload)
            records.extend(
                self._records(
                    payload,
                    ("records", "activities", "dailyData", "sleepData", "items", "data", "list"),
                )
            )
            has_more, next_cursor = self._pagination_metadata(payload)
            if not has_more:
                return records
            if cursor_key is not None and next_cursor not in (None, ""):
                arguments[cursor_key] = next_cursor
                continue
            if page_number_key is not None:
                arguments[page_number_key] = page_index + 2
                continue
            raise CorosMcpSchemaError("COROS 返回了未完成分页，但工具参数无法安全翻页")
        raise CorosMcpSchemaError("COROS 分页超过安全上限，拒绝使用不完整运动数据")

    def _required_tool(self, tools: dict[str, Any], source: str) -> Any:
        name = self._TOOL_NAMES[source]
        tool = tools.get(name)
        if tool is None:
            raise CorosMcpSchemaError(f"COROS 未提供必需工具 {name}")
        return tool

    async def _fetch_async(
        self, credential: CorosAccessCredential, start_date: date, end_date: date
    ) -> LiveFitnessData:
        async def operation(tools: dict[str, Any]) -> LiveFitnessData:
            sources = self._CORE_SOURCES
            raw: dict[str, list[dict[str, Any]]] = {}
            unavailable: list[str] = []
            for source in sources:
                try:
                    tool = self._required_tool(tools, source)
                    raw[source] = await self._read_all_pages(source, tool, start_date, end_date)
                except CorosMcpUnauthorizedError:
                    raise
                except CorosMcpError as error:
                    logger.warning(
                        "COROS_MCP_SOURCE_UNAVAILABLE source=%s error_type=%s reason=%s",
                        source,
                        type(error).__name__,
                        str(error),
                    )
                    unavailable.append(source)
            if len(unavailable) == len(sources):
                raise CorosMcpUnavailableError("COROS 未返回可用运动数据")
            for source in self._ENRICHMENT_SOURCES:
                tool = tools.get(self._TOOL_NAMES[source])
                if tool is None:
                    continue
                try:
                    raw[source] = await self._read_all_pages(source, tool, start_date, end_date)
                except CorosMcpUnauthorizedError:
                    raise
                except CorosMcpError as error:
                    logger.warning(
                        "COROS_MCP_SOURCE_UNAVAILABLE source=%s error_type=%s reason=%s",
                        source,
                        type(error).__name__,
                        str(error),
                    )
                    unavailable.append(source)
            activities = self._normalize_many(raw.get("activities", []), self._normalize_activity)
            daily_metrics = self._merge_daily_metrics(
                raw.get(source, []) for source in ("daily", *self._ENRICHMENT_SOURCES)
            )
            sleep_records = self._normalize_many(raw.get("sleep", []), self._normalize_sleep)
            activities = [
                record
                for record in activities
                if self._within_date_range(record, start_date, end_date)
            ]
            daily_metrics = [
                record
                for record in daily_metrics
                if self._within_date_range(record, start_date, end_date)
            ]
            sleep_records = [
                record
                for record in sleep_records
                if self._within_date_range(record, start_date, end_date)
            ]
            normalized_by_source = {
                "activities": activities,
                "daily": daily_metrics,
                "sleep": sleep_records,
                "training_load": daily_metrics,
                "resting_heart_rate": daily_metrics,
                "sleep_hrv": daily_metrics,
            }
            for source, records in raw.items():
                if records and not normalized_by_source[source] and source not in unavailable:
                    logger.warning(
                        "COROS_MCP_SOURCE_NORMALIZATION_FAILED source=%s record_count=%s",
                        source,
                        len(records),
                    )
                    unavailable.append(source)
            if all(source in unavailable for source in sources):
                raise CorosMcpUnavailableError("COROS 返回的数据格式不兼容")
            return LiveFitnessData(
                start_date=start_date,
                end_date=end_date,
                activities=activities,
                daily_metrics=daily_metrics,
                sleep_records=sleep_records,
                unavailable_sources=sorted(unavailable),
            )

        return await self._with_tools(credential, operation)

    def fetch_snapshot(
        self, db: DBSession, *, user_id: int, start_date: date, end_date: date
    ) -> LiveFitnessData:
        """读取一个日期范围的实时快照；只为 401 做一次强制刷新重试。"""

        if start_date > end_date:
            raise ValueError("start_date 不能晚于 end_date")
        for attempt in range(2):
            credential = self._oauth.get_access_credential(
                db, user_id=user_id, force_refresh=attempt == 1
            )
            try:
                return asyncio.run(self._fetch_async(credential, start_date, end_date))
            except CorosMcpUnauthorizedError:
                if attempt == 0:
                    continue
                self._oauth.mark_reconnection_required(db, user_id=user_id)
                raise CorosReconnectionRequiredError("COROS 授权已失效，请重新连接")
        raise CorosMcpUnavailableError("COROS 未返回可用运动数据")

    def fetch_activity_detail(
        self, db: DBSession, *, user_id: int, activity_id: str, activity_date: date
    ) -> dict[str, Any] | None:
        """先在同日实时活动中验证选择，再读取一项活动详情。"""

        if not activity_id:
            return None
        candidates = self.fetch_snapshot(
            db,
            user_id=user_id,
            start_date=activity_date,
            end_date=activity_date,
        )
        if not any(
            record.get("external_id") == activity_id
            and record.get("date") == activity_date.isoformat()
            for record in candidates.activities
        ):
            return None
        for attempt in range(2):
            credential = self._oauth.get_access_credential(
                db, user_id=user_id, force_refresh=attempt == 1
            )
            try:
                return asyncio.run(self._fetch_activity_detail_async(credential, activity_id))
            except CorosMcpUnauthorizedError:
                if attempt == 0:
                    continue
                self._oauth.mark_reconnection_required(db, user_id=user_id)
                raise CorosReconnectionRequiredError("COROS 授权已失效，请重新连接")
        return None

    async def _fetch_activity_detail_async(
        self, credential: CorosAccessCredential, activity_id: str
    ) -> dict[str, Any] | None:
        async def operation(tools: dict[str, Any]) -> dict[str, Any] | None:
            tool = self._required_tool(tools, "activity_detail")
            result = await self._call_tool(tool, self._activity_arguments(tool, activity_id))
            payload = self._decode_result(result)
            if isinstance(payload, dict):
                return self._normalize_activity(payload)
            records = self._records(payload, ("activity", "record", "data"))
            return self._normalize_activity(records[0]) if records else None

        return await self._with_tools(credential, operation)


@lru_cache(maxsize=1)
def get_coros_live_gateway() -> CorosLiveGateway:
    """复用无用户状态的 Gateway；凭据只在方法调用中从数据库读取。"""

    return CorosLiveGateway()
