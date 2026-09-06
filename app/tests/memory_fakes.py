"""Memory tests use this in-process substitute for the external backend."""

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid


class FakeMemoryBackend:
    """Keep provider state in memory while testing the real application policy."""

    def __init__(self):
        self.extracted = []
        self.entries = {}
        self.hits = None
        self.fail = False
        self.extraction_inputs = []

    def _check(self):
        if self.fail:
            raise TimeoutError("private provider payload must not be exposed")

    def create(self, *, user_id, text, metadata):
        self._check()
        record = SimpleNamespace(
            id=str(uuid.uuid4()),
            user_id=user_id,
            text=text,
            metadata=dict(metadata),
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
            score=0.9,
        )
        self.entries[record.id] = record
        return record

    def extract(self, *, user_id, message_id, text, session_id=None):
        self._check()
        self.extraction_inputs.append((user_id, message_id, text))
        old = [
            r
            for r in self.entries.values()
            if r.user_id == user_id and r.metadata.get("source_message_id") == message_id
        ]
        if old:
            return old
        return [
            self.create(
                user_id=user_id,
                text=t,
                metadata={"status": "proposed", "source": "chat", "source_message_id": message_id},
            )
            for t in dict.fromkeys(self.extracted)
        ]

    def list(self, *, user_id, include_revoked=False):
        self._check()
        return [
            r
            for r in self.entries.values()
            if r.user_id == user_id and (include_revoked or r.metadata.get("status") != "revoked")
        ]

    def get(self, *, user_id, memory_id):
        self._check()
        r = self.entries.get(memory_id)
        return r if r and r.user_id == user_id else None

    def update(self, *, user_id, memory_id, text=None, metadata):
        r = self.get(user_id=user_id, memory_id=memory_id)
        if r is None:
            raise ValueError("not found")
        if text is not None:
            r.text = text
        r.metadata.update(metadata)
        r.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        return r

    def search(self, *, user_id, query, limit):
        self._check()
        if self.hits is not None:
            return self.hits[:limit]
        return [
            r
            for r in self.entries.values()
            if r.user_id == user_id and r.metadata.get("status") == "confirmed"
        ][:limit]
