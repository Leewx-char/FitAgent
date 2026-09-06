"""Domain contract for the replaceable long-term memory backend."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """Provider-neutral long-term memory returned to application services."""

    id: str
    user_id: int
    text: str
    metadata: dict[str, object]
    created_at: datetime
    updated_at: datetime
    score: float | None = None


class MemoryBackend(Protocol):
    """Operations required by the application memory lifecycle."""

    def extract(
        self,
        *,
        user_id: int,
        message_id: int,
        text: str,
        session_id: str | None = None,
    ) -> list[MemoryRecord]:
        """Extract proposed memories from one isolated user message."""

    def create(self, *, user_id: int, text: str, metadata: dict[str, object]) -> MemoryRecord:
        """Store one exact memory without provider inference."""

    def list(self, *, user_id: int, include_revoked: bool = False) -> list[MemoryRecord]:
        """List memories owned by one user."""

    def get(self, *, user_id: int, memory_id: str) -> MemoryRecord | None:
        """Return an owned memory, hiding missing and foreign records alike."""

    def update(
        self,
        *,
        user_id: int,
        memory_id: str,
        text: str | None = None,
        metadata: dict[str, object],
    ) -> MemoryRecord:
        """Update the text or metadata of an owned memory."""

    def search(self, *, user_id: int, query: str, limit: int) -> list[MemoryRecord]:
        """Search confirmed memories owned by one user."""


_backend: MemoryBackend | None = None
_backend_lock = Lock()


def get_memory_backend() -> MemoryBackend:
    """Construct one SDK adapter lazily, including under concurrent first use."""

    global _backend
    if _backend is None:
        with _backend_lock:
            if _backend is None:
                from app.integrations.mem0_backend import Mem0Backend

                _backend = Mem0Backend.from_settings()
    return _backend
