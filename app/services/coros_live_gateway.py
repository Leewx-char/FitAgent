"""通过官方远程 MCP 按需读取并最小化 COROS 运动数据。"""

from __future__ import annotations

import asyncio
import json
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
        "activity_detail": "getActivityDetail",
    }
    _DATE_START_KEYS = ("startDate", "start_date", "startDay", "start_day", "fromDate")
    _DATE_END_KEYS = ("endDate", "end_date", "endDay", "end_day", "toDate")
    _ACTIVITY_ID_KEYS = ("activityId", "activity_id", "sportRecordId", "recordId", "id")

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
        fields = cls._tool_fields(tool)
        start_key = cls._find_field(fields, cls._DATE_START_KEYS)
        end_key = cls._find_field(fields, cls._DATE_END_KEYS)
        if start_key is None and end_key is None:
            raise CorosMcpSchemaError("COROS 工具未声明可验证的日期范围参数")
        if start_key is None or end_key is None:
            raise CorosMcpSchemaError("COROS 工具日期参数不完整，暂无法安全调用")
        return {
            start_key: start_date.isoformat(),
            end_key: end_date.isoformat(),
        }

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
        return {
            "date": record_date,
            "rhr": cls._value(record, "rhr", "restingHeartRate"),
            "avg_sleep_hrv": cls._value(record, "avg_sleep_hrv", "sleepHrv", "avgSleepHrv"),
            "training_load": cls._value(record, "training_load", "trainingLoad"),
            "training_load_ratio": cls._value(record, "training_load_ratio", "trainingLoadRatio"),
            "tired_rate": cls._value(record, "tired_rate", "tiredRate", "fatigue"),
            "vo2max": cls._value(record, "vo2max", "vo2Max"),
        }

    @classmethod
    def _normalize_sleep(cls, record: dict[str, Any]) -> dict[str, Any] | None:
        record_date = cls._date_value(cls._value(record, "date", "day", "recordDate"))
        if not record_date:
            return None
        raw_phases = cls._value(record, "phases", "sleepStages", "stages")
        phases = raw_phases if isinstance(raw_phases, dict) else {}
        return {
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

    @classmethod
    def _normalize_activity(cls, record: dict[str, Any]) -> dict[str, Any] | None:
        external_id = cls._value(record, "activityId", "activity_id", "id", "recordId", "uuid")
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
            if CorosLiveGateway._is_unauthorized(error):
                raise CorosMcpUnauthorizedError("COROS MCP 授权已失效") from error
            raise CorosMcpError("COROS MCP 数据读取失败，请稍后重试") from error
        if getattr(result, "status", None) == "error":
            raise CorosMcpError("COROS MCP 数据读取失败，请稍后重试")
        return result

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
            sources = ("activities", "daily", "sleep")
            raw: dict[str, list[dict[str, Any]]] = {}
            unavailable: list[str] = []
            for source in sources:
                try:
                    tool = self._required_tool(tools, source)
                    result = await self._call_tool(
                        tool, self._date_arguments(tool, start_date, end_date)
                    )
                    raw[source] = self._records(
                        self._decode_result(result),
                        (
                            "records",
                            "activities",
                            "dailyData",
                            "sleepData",
                            "items",
                            "data",
                            "list",
                        ),
                    )
                except CorosMcpUnauthorizedError:
                    raise
                except CorosMcpError:
                    unavailable.append(source)
            if len(unavailable) == len(sources):
                raise CorosMcpUnavailableError("COROS 未返回可用运动数据")
            activities = self._normalize_many(raw.get("activities", []), self._normalize_activity)
            daily_metrics = self._normalize_many(raw.get("daily", []), self._normalize_daily)
            sleep_records = self._normalize_many(raw.get("sleep", []), self._normalize_sleep)
            normalized_by_source = {
                "activities": activities,
                "daily": daily_metrics,
                "sleep": sleep_records,
            }
            for source, records in raw.items():
                if records and not normalized_by_source[source] and source not in unavailable:
                    unavailable.append(source)
            if len(unavailable) == len(sources):
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
                raise CorosReconnectionRequiredError("COROS 授权已失效，请重新连接")
        raise CorosMcpUnavailableError("COROS 未返回可用运动数据")

    def fetch_activity_detail(
        self, db: DBSession, *, user_id: int, activity_id: str
    ) -> dict[str, Any] | None:
        """读取用户已选择的一次活动详情，结果只在当前请求中返回。"""

        if not activity_id:
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
