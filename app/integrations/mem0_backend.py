"""mem0 2.0 adapter; SDK-specific types and payloads stop at this boundary."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from app.core.settings import Settings, get_settings
from app.services.memory_backend import MemoryRecord
from app.utils.config_handler import get_models_config, get_vector_store_config


EXTRACTION_INSTRUCTIONS = """\
只从本次用户消息中提取值得长期保留、明确陈述的中文事实。每条 memory 只写一个事实，保持简短，\
不要推断，不要写助手内容或临时寒暄。可归类时必须使用以下前缀之一：city:、training_goal:、\
injuries:、diet_pref:、preference:。无法归类但确有长期价值时允许输出简短纯文本。\
若没有可保存事实，返回空 memory 数组。"""
_MEM0_LOGGER_NAME = "mem0"
_SAFE_SDK_LOGGER_NAME = f"{__name__}.sdk"
_LOGGING_CONFIG_LOCK = Lock()


class MemoryListOverflowError(RuntimeError):
    """The configured bounded listing cannot represent every matching memory."""


class _SanitizedMem0Handler(logging.Handler):
    """Forward SDK event location and severity without private log payloads."""

    def emit(self, record: logging.LogRecord) -> None:
        message = (
            f"mem0_sdk_event source={record.name} module={record.module} "
            f"function={record.funcName} line={record.lineno}"
        )
        logging.getLogger(_SAFE_SDK_LOGGER_NAME).log(record.levelno, message)


def _configure_mem0_logging() -> None:
    """Replace mem0 namespace output with one idempotent sanitized forwarding handler."""

    with _LOGGING_CONFIG_LOCK:
        sdk_logger = logging.getLogger(_MEM0_LOGGER_NAME)
        sanitized = next(
            (
                handler
                for handler in sdk_logger.handlers
                if isinstance(handler, _SanitizedMem0Handler)
            ),
            None,
        )
        for handler in tuple(sdk_logger.handlers):
            if handler is not sanitized:
                sdk_logger.removeHandler(handler)
        if sanitized is None:
            sdk_logger.addHandler(_SanitizedMem0Handler())
        sdk_logger.propagate = False


class _TimedDashScopeClient:
    """Add a finite timeout to LangChain's DashScope embedding calls."""

    def __init__(self, client: Any, timeout_seconds: float) -> None:
        self._client = client
        self._timeout_seconds = timeout_seconds

    def call(self, **kwargs: Any) -> Any:
        kwargs.setdefault("request_timeout", self._timeout_seconds)
        return self._client.call(**kwargs)


class Mem0Backend:
    """Implement the application memory contract using one mem0 collection."""

    def __init__(
        self,
        *,
        memory: Any,
        settings: Settings,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        _configure_mem0_logging()
        self._memory = memory
        self._settings = settings
        self._now = now or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Mem0Backend:
        """Initialize mem0 after storage, privacy and provider settings are resolved."""

        resolved = settings or get_settings()
        storage_path = _resolve_storage_path(resolved)
        storage_path.mkdir(parents=True, exist_ok=True)

        # mem0 reads both variables while importing and otherwise writes to ~/.mem0.
        os.environ["MEM0_DIR"] = str(storage_path)
        os.environ["MEM0_TELEMETRY"] = "false"
        _configure_mem0_logging()

        memory = _create_mem0_memory(settings=resolved, storage_path=storage_path)
        return cls(memory=memory, settings=resolved)

    def extract(
        self,
        *,
        user_id: int,
        message_id: int,
        text: str,
        session_id: str | None = None,
    ) -> list[MemoryRecord]:
        """Extract once per message while retaining user-only history for its chat session."""

        run_id = f"session:{session_id}" if session_id else f"message:{message_id}"
        source_filters = {
            "user_id": str(user_id),
            "source": "chat",
            "source_message_id": message_id,
        }
        existing = self._list_records(filters=source_filters, include_expired=True)
        if existing:
            return existing

        expiry = self._now() + timedelta(days=self._settings.memory_default_ttl_days)
        metadata: dict[str, object] = {
            "status": "proposed",
            "source": "chat",
            "source_message_id": message_id,
            "expires_at": expiry.isoformat(),
        }
        self._memory.add(
            [{"role": "user", "content": text}],
            user_id=str(user_id),
            run_id=run_id,
            metadata=metadata,
            infer=True,
            prompt=EXTRACTION_INSTRUCTIONS,
        )
        return self._list_records(filters=source_filters, include_expired=True)

    def create(self, *, user_id: int, text: str, metadata: dict[str, object]) -> MemoryRecord:
        """Persist exact text, defaulting missing authorization state to proposed."""

        safe_metadata = dict(metadata)
        safe_metadata.setdefault("status", "proposed")
        legacy_id = safe_metadata.get("legacy_id")
        if legacy_id is not None:
            existing = self._list_records(
                filters={"user_id": str(user_id), "legacy_id": legacy_id},
                include_expired=True,
            )
            if existing:
                return existing[0]

        result = self._memory.add(
            text,
            user_id=str(user_id),
            metadata=safe_metadata,
            infer=False,
        )
        memory_id = self._result_id(result)
        created = self.get(user_id=user_id, memory_id=memory_id)
        if created is None:
            raise RuntimeError("mem0 did not return the created memory")
        return created

    def list(self, *, user_id: int, include_revoked: bool = False) -> list[MemoryRecord]:
        """Return a bounded, user-scoped list and never silently truncate it."""

        filters: dict[str, object] = {"user_id": str(user_id)}
        if not include_revoked:
            filters["status"] = {"ne": "revoked"}
        return self._list_records(filters=filters, include_expired=True)

    def get(self, *, user_id: int, memory_id: str) -> MemoryRecord | None:
        """Fetch by SDK id, then enforce ownership before exposing the record."""

        raw = self._memory.get(memory_id)
        if raw is None:
            return None
        record = self._to_record(raw)
        return record if record.user_id == user_id else None

    def update(
        self,
        *,
        user_id: int,
        memory_id: str,
        text: str | None = None,
        metadata: dict[str, object],
    ) -> MemoryRecord:
        """Update an owned record; mem0 2.0.20 re-embeds existing text for metadata updates."""

        if self.get(user_id=user_id, memory_id=memory_id) is None:
            raise LookupError("memory does not exist")
        self._memory.update(memory_id, text=text, metadata=dict(metadata))
        updated = self.get(user_id=user_id, memory_id=memory_id)
        if updated is None:
            raise RuntimeError("mem0 memory disappeared during update")
        return updated

    def search(self, *, user_id: int, query: str, limit: int) -> list[MemoryRecord]:
        """Search only confirmed memories in the caller's tenant scope."""

        result = self._memory.search(
            query,
            top_k=limit,
            filters={"user_id": str(user_id), "status": "confirmed"},
            threshold=self._settings.memory_score_threshold,
            show_expired=True,
        )
        return [self._to_record(item) for item in _result_rows(result)]

    def _list_records(
        self, *, filters: dict[str, object], include_expired: bool
    ) -> list[MemoryRecord]:
        max_items = self._settings.memory_max_list_items
        result = self._memory.get_all(
            filters=filters,
            top_k=max_items + 1,
            show_expired=include_expired,
        )
        rows = _result_rows(result)
        if len(rows) > max_items:
            raise MemoryListOverflowError(
                f"memory list exceeds configured maximum of {max_items} items"
            )
        return [self._to_record(item) for item in rows]

    @staticmethod
    def _result_id(result: object) -> str:
        rows = _result_rows(result)
        if not rows or not rows[0].get("id"):
            raise RuntimeError("mem0 add returned no memory id")
        return str(rows[0]["id"])

    @staticmethod
    def _to_record(raw: Mapping[str, object]) -> MemoryRecord:
        metadata_value = raw.get("metadata")
        metadata = dict(metadata_value) if isinstance(metadata_value, Mapping) else {}
        return MemoryRecord(
            id=str(raw["id"]),
            user_id=int(str(raw["user_id"])),
            text=str(raw.get("memory", "")),
            metadata=metadata,
            created_at=_utc_naive(raw.get("created_at")),
            updated_at=_utc_naive(raw.get("updated_at")),
            score=float(raw["score"]) if raw.get("score") is not None else None,
        )


def _result_rows(result: object) -> list[Mapping[str, object]]:
    """Validate the stable dictionary envelope returned by mem0 2.0.20."""

    if not isinstance(result, Mapping):
        raise RuntimeError("mem0 returned an invalid response")
    rows = result.get("results")
    if not isinstance(rows, list):
        raise RuntimeError("mem0 returned an invalid results envelope")
    if not all(isinstance(row, Mapping) for row in rows):
        raise RuntimeError("mem0 returned an invalid memory row")
    return rows


def _utc_naive(value: object) -> datetime:
    """Normalize mem0 timestamps to the application's naive-UTC convention."""

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value)
    else:
        raise RuntimeError("mem0 memory is missing a valid timestamp")
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _resolve_storage_path(settings: Settings) -> Path:
    configured = Path(settings.memory_storage_path).expanduser()
    if configured.is_absolute():
        return configured
    return (settings.project_root / configured).resolve()


def _create_mem0_memory(*, settings: Settings, storage_path: Path) -> Any:
    """Build the real SDK with project DashScope models and a bounded Qdrant client."""

    from langchain_community.chat_models.tongyi import ChatTongyi
    from langchain_community.embeddings import DashScopeEmbeddings
    from mem0 import Memory
    from qdrant_client import QdrantClient

    api_key = settings.dashscope_api_key.strip()
    if not api_key:
        raise EnvironmentError("缺少 .env 配置 DASHSCOPE_API_KEY，无法初始化长期记忆。")

    model_config = get_models_config()
    llm_model_name = settings.memory_llm_model.strip() or model_config["chat_model_name"]
    embedding_model_name = (
        settings.memory_embedding_model.strip() or model_config["embedding_model_name"]
    )
    llm = ChatTongyi(
        model=llm_model_name,
        streaming=False,
        api_key=api_key,
        max_retries=settings.memory_max_retries,
        model_kwargs={"request_timeout": settings.memory_timeout_seconds},
    )
    embedder = DashScopeEmbeddings(
        model=embedding_model_name,
        dashscope_api_key=api_key,
        max_retries=settings.memory_max_retries,
    )
    embedder.client = _TimedDashScopeClient(embedder.client, settings.memory_timeout_seconds)

    vector_config = get_vector_store_config()
    qdrant_url = settings.qdrant_url.strip() or str(vector_config["url"])
    qdrant_client = QdrantClient(
        url=qdrant_url,
        api_key=settings.qdrant_api_key.strip() or None,
        timeout=settings.memory_timeout_seconds,
        prefer_grpc=bool(vector_config.get("prefer_grpc", False)),
        grpc_port=int(vector_config.get("grpc_port", 6334)),
    )
    config = {
        "version": "v1.1",
        "history_db_path": str(storage_path / "history.db"),
        "custom_instructions": EXTRACTION_INSTRUCTIONS,
        "llm": {"provider": "langchain", "config": {"model": llm}},
        "embedder": {
            "provider": "langchain",
            "config": {
                "model": embedder,
                "embedding_dims": settings.memory_embedding_dimensions,
            },
        },
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": f"{settings.memory_collection_prefix}_main",
                "embedding_model_dims": settings.memory_embedding_dimensions,
                "client": qdrant_client,
                # mem0 2.0.20's validator requires a location even when client is supplied.
                "path": str(storage_path / "qdrant-client-placeholder"),
            },
        },
    }
    return Memory.from_config(config)
