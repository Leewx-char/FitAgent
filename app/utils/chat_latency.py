"""聊天请求的分段耗时记录，避免将用户原文写入性能日志。"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from app.utils.logger_handler import logger


class ChatLatencyTracker:
    """以单个请求为范围记录链路节点的累计与局部耗时。"""

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self._started_at = time.perf_counter()
        self._once_keys: set[str] = set()
        self._lock = threading.Lock()

    def mark(self, stage: str, **fields: str | int | bool) -> None:
        """记录一个瞬时节点；字段只能是非敏感的结构化元数据。"""
        self._emit(stage, elapsed_ms=0, **fields)

    def mark_once(self, key: str, stage: str, **fields: str | int | bool) -> bool:
        """每个键只输出一次，用于首个事件和首个文本分片。"""
        with self._lock:
            if key in self._once_keys:
                return False
            self._once_keys.add(key)
        self.mark(stage, **fields)
        return True

    def record_duration(self, stage: str, elapsed_ms: int, **fields: str | int | bool) -> None:
        """记录外部组件已经测得的耗时，保持同一请求关联。"""
        self._emit(stage, elapsed_ms=max(0, elapsed_ms), **fields)

    @contextmanager
    def span(self, stage: str, **fields: str | int | bool) -> Iterator[None]:
        """测量同步代码段；异常时仅记录异常类型而不泄露异常正文。"""
        started_at = time.perf_counter()
        try:
            yield
        except Exception as error:
            self._emit(
                stage,
                elapsed_ms=round((time.perf_counter() - started_at) * 1000),
                status="error",
                error_type=type(error).__name__,
                **fields,
            )
            raise
        else:
            self._emit(
                stage,
                elapsed_ms=round((time.perf_counter() - started_at) * 1000),
                status="ok",
                **fields,
            )

    def _emit(self, stage: str, *, elapsed_ms: int, **fields: str | int | bool) -> None:
        """输出单行 JSON，便于按 request_id 聚合分析。"""
        payload = {
            "request_id": self.request_id,
            "stage": stage,
            "elapsed_ms": elapsed_ms,
            "total_ms": round((time.perf_counter() - self._started_at) * 1000),
            **fields,
        }
        logger.info("CHAT_LATENCY %s", json.dumps(payload, ensure_ascii=False))
