"""Behavioral contract tests for the mem0 SDK adapter."""

from __future__ import annotations

import os
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import ANY

import pytest

from app.integrations.mem0_backend import (
    EXTRACTION_INSTRUCTIONS,
    Mem0Backend,
    MemoryListOverflowError,
)
from app.services.memory_backend import MemoryRecord


FIXED_NOW = datetime(2026, 9, 5, 8, 30, tzinfo=timezone.utc)


def settings(**overrides):
    values = {
        "memory_default_ttl_days": 90,
        "memory_max_list_items": 3,
        "memory_score_threshold": 0.2,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeMem0:
    """Small provider double that mirrors mem0 2.0.20's public result shape."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.add_calls: list[dict] = []
        self.get_all_calls: list[dict] = []
        self.search_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.inferred_texts: list[str] = []

    @staticmethod
    def _matches(row, filters):
        available = {**row.get("metadata", {}), **row}
        for key, expected in filters.items():
            actual = available.get(key)
            if isinstance(expected, dict) and "ne" in expected:
                if actual == expected["ne"]:
                    return False
            elif actual != expected:
                return False
        return True

    def add(self, messages, **kwargs):
        self.add_calls.append({"messages": messages, **kwargs})
        texts = (
            self.inferred_texts
            if kwargs["infer"]
            else [messages if isinstance(messages, str) else messages[0]["content"]]
        )
        results = []
        for text in texts:
            memory_id = f"mem-{len(self.rows) + 1}"
            stamp = FIXED_NOW.isoformat()
            self.rows[memory_id] = {
                "id": memory_id,
                "memory": text,
                "user_id": kwargs["user_id"],
                "run_id": kwargs.get("run_id"),
                "metadata": dict(kwargs.get("metadata", {})),
                "created_at": stamp,
                "updated_at": stamp,
            }
            results.append({"id": memory_id, "memory": text, "event": "ADD"})
        return {"results": results}

    def get_all(self, *, filters, top_k, show_expired=False):
        self.get_all_calls.append(
            {
                "filters": filters,
                "top_k": top_k,
                "show_expired": show_expired,
            }
        )
        rows = [row.copy() for row in self.rows.values() if self._matches(row, filters)]
        return {"results": rows[:top_k]}

    def get(self, memory_id):
        row = self.rows.get(memory_id)
        return row.copy() if row else None

    def update(self, memory_id, *, text=None, metadata=None):
        self.update_calls.append({"memory_id": memory_id, "text": text, "metadata": metadata})
        row = self.rows[memory_id]
        if text is not None:
            row["memory"] = text
        row["metadata"] = {**row["metadata"], **(metadata or {})}
        row["updated_at"] = (FIXED_NOW + timedelta(minutes=1)).isoformat()
        return {"message": "Memory updated successfully!"}

    def search(self, query, **kwargs):
        self.search_calls.append({"query": query, **kwargs})
        rows = [row.copy() for row in self.rows.values() if self._matches(row, kwargs["filters"])]
        for index, row in enumerate(rows):
            row["score"] = 0.9 - index / 10
        return {"results": rows[: kwargs["top_k"]]}


def backend(provider=None, **setting_overrides):
    provider = provider or FakeMem0()
    return Mem0Backend(
        memory=provider,
        settings=settings(**setting_overrides),
        now=lambda: FIXED_NOW,
    )


def test_extract_uses_one_user_message_and_message_scoped_run():
    provider = FakeMem0()
    provider.inferred_texts = ["city: 杭州", "training_goal: 完成半马"]
    adapter = backend(provider)

    records = adapter.extract(user_id=7, message_id=42, text="我住杭州，准备完成半马")

    assert [record.text for record in records] == ["city: 杭州", "training_goal: 完成半马"]
    assert all(
        record.metadata
        == {
            "status": "proposed",
            "source": "chat",
            "source_message_id": 42,
            "expires_at": "2026-12-04T08:30:00+00:00",
        }
        for record in records
    )
    assert provider.add_calls == [
        {
            "messages": [{"role": "user", "content": "我住杭州，准备完成半马"}],
            "user_id": "7",
            "run_id": "message:42",
            "metadata": {
                "status": "proposed",
                "source": "chat",
                "source_message_id": 42,
                "expires_at": "2026-12-04T08:30:00+00:00",
            },
            "infer": True,
            "prompt": ANY,
        }
    ]


def test_extract_retry_returns_existing_message_rows_including_revoked():
    provider = FakeMem0()
    provider.inferred_texts = ["city: 成都"]
    adapter = backend(provider)
    first = adapter.extract(user_id=3, message_id=8, text="我住成都")
    provider.rows[first[0].id]["metadata"]["status"] = "revoked"

    retried = adapter.extract(user_id=3, message_id=8, text="重试时文本也不能触发重发")

    assert [record.id for record in retried] == [first[0].id]
    assert retried[0].metadata["status"] == "revoked"
    assert len(provider.add_calls) == 1


def test_extract_processes_distinct_messages_in_one_session_but_not_a_retry():
    provider = FakeMem0()
    adapter = backend(provider)
    provider.inferred_texts = ["city: 成都"]
    first = adapter.extract(user_id=3, message_id=8, session_id="chat-a", text="我住成都")
    provider.inferred_texts = ["training_goal: 半马"]
    second = adapter.extract(user_id=3, message_id=9, session_id="chat-a", text="我要跑半马")
    retried = adapter.extract(user_id=3, message_id=8, session_id="chat-a", text="重试")

    assert first[0].id != second[0].id
    assert retried[0].id == first[0].id
    assert [call["run_id"] for call in provider.add_calls] == ["session:chat-a", "session:chat-a"]


def test_create_is_exact_and_missing_status_fails_closed():
    provider = FakeMem0()
    adapter = backend(provider)

    record = adapter.create(user_id=5, text="偏好低强度晨练", metadata={"source": "user"})

    assert record.metadata["status"] == "proposed"
    assert provider.add_calls[0] == {
        "messages": "偏好低强度晨练",
        "user_id": "5",
        "metadata": {"source": "user", "status": "proposed"},
        "infer": False,
    }


def test_legacy_create_is_idempotent_by_owner_and_legacy_id():
    provider = FakeMem0()
    adapter = backend(provider)
    metadata = {"status": "confirmed", "source": "legacy", "legacy_id": "old-19"}

    first = adapter.create(user_id=5, text="旧记忆", metadata=metadata)
    second = adapter.create(user_id=5, text="重试文本", metadata=metadata)

    assert second.id == first.id
    assert second.text == "旧记忆"
    assert len(provider.add_calls) == 1


def test_list_scopes_user_hides_revoked_and_detects_overflow():
    provider = FakeMem0()
    adapter = backend(provider)
    kept = adapter.create(user_id=1, text="保留", metadata={"status": "confirmed"})
    adapter.create(user_id=1, text="隐藏", metadata={"status": "revoked"})
    adapter.create(user_id=2, text="他人", metadata={"status": "confirmed"})

    assert [row.id for row in adapter.list(user_id=1)] == [kept.id]
    assert provider.get_all_calls[-1]["filters"] == {
        "user_id": "1",
        "status": {"ne": "revoked"},
    }

    adapter.create(user_id=1, text="二", metadata={"status": "confirmed"})
    adapter.create(user_id=1, text="三", metadata={"status": "confirmed"})
    adapter.create(user_id=1, text="四", metadata={"status": "confirmed"})
    with pytest.raises(MemoryListOverflowError):
        adapter.list(user_id=1)


def test_get_and_update_verify_owner_and_return_provider_neutral_record():
    provider = FakeMem0()
    adapter = backend(provider)
    row = adapter.create(user_id=11, text="偏好夜跑", metadata={"status": "proposed"})

    assert adapter.get(user_id=12, memory_id=row.id) is None
    with pytest.raises(LookupError):
        adapter.update(user_id=12, memory_id=row.id, metadata={"status": "confirmed"})

    updated = adapter.update(
        user_id=11, memory_id=row.id, text="偏好晨跑", metadata={"status": "confirmed"}
    )
    assert isinstance(updated, MemoryRecord)
    assert updated.text == "偏好晨跑"
    assert updated.metadata["status"] == "confirmed"
    assert updated.created_at == datetime(2026, 9, 5, 8, 30)
    assert updated.updated_at == datetime(2026, 9, 5, 8, 31)


def test_search_enforces_confirmed_owner_filter_threshold_and_limit():
    provider = FakeMem0()
    adapter = backend(provider, memory_score_threshold=0.35)
    confirmed = adapter.create(user_id=4, text="膝盖旧伤", metadata={"status": "confirmed"})
    adapter.create(user_id=4, text="未确认", metadata={"status": "proposed"})
    adapter.create(user_id=9, text="他人", metadata={"status": "confirmed"})

    hits = adapter.search(user_id=4, query="训练伤病", limit=2)

    assert [hit.id for hit in hits] == [confirmed.id]
    assert hits[0].score == 0.9
    assert provider.search_calls == [
        {
            "query": "训练伤病",
            "top_k": 2,
            "filters": {"user_id": "4", "status": "confirmed"},
            "threshold": 0.35,
            "show_expired": True,
        }
    ]


def test_mem0_log_sanitizer_is_scoped_and_idempotent(caplog):
    provider = FakeMem0()
    backend(provider)
    backend(provider)
    secret = "SYNTHETIC_PRIVATE_PROVIDER_DETAIL"

    with caplog.at_level(logging.INFO):
        logging.getLogger("mem0.memory.main").warning("provider failed: %s", secret)
        logging.getLogger("unrelated.provider").warning("unrelated detail remains visible")

    safe_events = [
        record for record in caplog.records if record.name == "app.integrations.mem0_backend.sdk"
    ]
    assert len(safe_events) == 1
    assert safe_events[0].levelno == logging.WARNING
    assert "source=mem0.memory.main" in safe_events[0].getMessage()
    assert (
        "function=test_mem0_log_sanitizer_is_scoped_and_idempotent" in safe_events[0].getMessage()
    )
    assert secret not in caplog.text
    assert "unrelated detail remains visible" in caplog.text


def test_lazy_factory_creates_one_backend_under_concurrent_first_use(monkeypatch):
    import app.services.memory_backend as contract

    sentinel = object()
    creations = []

    def create_once(cls):
        creations.append(1)
        return sentinel

    monkeypatch.setattr(contract, "_backend", None)
    monkeypatch.setattr(Mem0Backend, "from_settings", classmethod(create_once))
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: contract.get_memory_backend(), range(24)))

    assert all(result is sentinel for result in results)
    assert creations == [1]


def test_from_settings_builds_private_bounded_single_store(monkeypatch, tmp_path):
    monkeypatch.setenv("MEM0_DIR", str(tmp_path / "pre-import"))
    monkeypatch.setenv("MEM0_TELEMETRY", "false")
    import mem0

    captured = {}
    provider = object()

    def capture_config(cls, config):
        captured["config"] = config
        return provider

    monkeypatch.setattr(mem0.Memory, "from_config", classmethod(capture_config))
    configured = SimpleNamespace(
        project_root=tmp_path,
        memory_storage_path="private-memory",
        memory_llm_model="qwen-test",
        memory_embedding_model="text-embedding-v1",
        memory_embedding_dimensions=1536,
        memory_timeout_seconds=7.0,
        memory_max_retries=2,
        memory_collection_prefix="isolated",
        dashscope_api_key="test-key",
        qdrant_url="http://127.0.0.1:6333",
        qdrant_api_key="",
    )

    adapter = Mem0Backend.from_settings(configured)
    config = captured["config"]

    assert adapter._memory is provider
    assert os.environ["MEM0_DIR"] == str(tmp_path / "private-memory")
    assert os.environ["MEM0_TELEMETRY"] == "false"
    assert config["history_db_path"] == str(tmp_path / "private-memory" / "history.db")
    assert config["vector_store"]["config"]["collection_name"] == "isolated_main"
    assert config["vector_store"]["config"]["embedding_model_dims"] == 1536
    assert config["llm"]["config"]["model"].model_name == "qwen-test"
    assert config["llm"]["config"]["model"].model_kwargs["request_timeout"] == 7.0
    assert config["llm"]["config"]["model"].max_retries == 2
    assert config["embedder"]["config"]["model"].model == "text-embedding-v1"
    config["vector_store"]["config"]["client"].close()


def test_real_mem0_2_0_20_local_qdrant_round_trip(monkeypatch, tmp_path):
    """Characterize the installed SDK APIs without external model or vector services."""

    monkeypatch.setenv("MEM0_DIR", str(tmp_path / "mem0-home"))
    monkeypatch.setenv("MEM0_TELEMETRY", "false")

    from langchain_core.embeddings import Embeddings
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from mem0 import Memory

    class FixedEmbeddings(Embeddings):
        def embed_documents(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

        def embed_query(self, text):
            return [1.0, 0.0, 0.0]

    model_inputs = []

    class RecordingFakeModel(FakeMessagesListChatModel):
        def invoke(self, input, config=None, **kwargs):
            model_inputs.append(input)
            return super().invoke(input, config=config, **kwargs)

    model = RecordingFakeModel(
        responses=[
            AIMessage(content='{"memory":[{"text":"city: 杭州"}]}'),
            AIMessage(content='{"memory":[{"text":"training_goal: 完成半马"}]}'),
        ]
    )
    sdk = Memory.from_config(
        {
            "version": "v1.1",
            "history_db_path": str(tmp_path / "history.db"),
            "custom_instructions": "只提取本条用户消息中的长期事实。",
            "llm": {"provider": "langchain", "config": {"model": model}},
            "embedder": {
                "provider": "langchain",
                "config": {"model": FixedEmbeddings(), "embedding_dims": 3},
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": "adapter_test",
                    "embedding_model_dims": 3,
                    "path": str(tmp_path / "qdrant"),
                },
            },
        }
    )
    adapter = backend(sdk, memory_max_list_items=20)

    try:
        proposed = adapter.extract(
            user_id=23, message_id=101, session_id="chat-live", text="我住杭州"
        )
        later = adapter.extract(
            user_id=23, message_id=102, session_id="chat-live", text="我要完成半马"
        )
        retried = adapter.extract(
            user_id=23, message_id=101, session_id="chat-live", text="同一消息的任务重试"
        )
        assert len(proposed) == 1
        assert [row.text for row in later] == ["training_goal: 完成半马"]
        assert retried[0].id == proposed[0].id
        assert proposed[0].metadata["status"] == "proposed"
        assert model_inputs[0][0][0] == "system"
        assert '"memory"' in model_inputs[0][0][1]
        assert EXTRACTION_INSTRUCTIONS in model_inputs[0][1][1]
        assert adapter.search(user_id=23, query="住在哪里", limit=3) == []

        confirmed = adapter.update(
            user_id=23,
            memory_id=proposed[0].id,
            metadata={"status": "confirmed"},
        )
        hits = adapter.search(user_id=23, query="住在哪里", limit=3)
        assert confirmed.text == "city: 杭州"
        assert [hit.id for hit in hits] == [confirmed.id]
        assert hits[0].score == pytest.approx(1.0)
        assert adapter.search(user_id=99, query="住在哪里", limit=3) == []

        adapter.update(
            user_id=23,
            memory_id=confirmed.id,
            metadata={"status": "revoked"},
        )
        adapter.update(
            user_id=23,
            memory_id=later[0].id,
            metadata={"status": "revoked"},
        )
        assert adapter.list(user_id=23) == []
        assert {row.id for row in adapter.list(user_id=23, include_revoked=True)} == {
            confirmed.id,
            later[0].id,
        }
    finally:
        sdk.close()
        sdk.vector_store.client.close()


def test_real_mem0_logs_hide_memory_text_and_provider_exception(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("MEM0_DIR", str(tmp_path / "mem0-home"))
    monkeypatch.setenv("MEM0_TELEMETRY", "false")

    from langchain_core.embeddings import Embeddings
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from mem0 import Memory
    from mem0.exceptions import LLMError

    class FixedEmbeddings(Embeddings):
        def embed_documents(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

        def embed_query(self, text):
            return [1.0, 0.0, 0.0]

    sdk = Memory.from_config(
        {
            "version": "v1.1",
            "history_db_path": str(tmp_path / "history.db"),
            "llm": {
                "provider": "langchain",
                "config": {
                    "model": FakeMessagesListChatModel(
                        responses=[AIMessage(content='{"memory":[]}')]
                    )
                },
            },
            "embedder": {
                "provider": "langchain",
                "config": {"model": FixedEmbeddings(), "embedding_dims": 3},
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": "safe_log_test",
                    "embedding_model_dims": 3,
                    "path": str(tmp_path / "qdrant"),
                },
            },
        }
    )
    adapter = backend(sdk, memory_max_list_items=20)
    row = adapter.create(user_id=1, text="initial", metadata={"status": "confirmed"})
    private_text = "SYNTHETIC_PRIVATE_MEMORY_TEXT"
    private_error = "SYNTHETIC_PRIVATE_PROVIDER_EXCEPTION"

    def fail_provider(**kwargs):
        raise RuntimeError(private_error)

    monkeypatch.setattr(sdk.llm, "generate_response", fail_provider)
    caplog.clear()
    try:
        with caplog.at_level(logging.INFO):
            adapter.update(
                user_id=1,
                memory_id=row.id,
                text=private_text,
                metadata={"status": "confirmed"},
            )
            with pytest.raises(LLMError):
                adapter.extract(
                    user_id=1,
                    message_id=909,
                    session_id="safe-log",
                    text="trigger extraction failure",
                )

        safe_events = [
            record
            for record in caplog.records
            if record.name == "app.integrations.mem0_backend.sdk"
        ]
        assert any(record.levelno == logging.INFO for record in safe_events)
        assert any(record.levelno == logging.ERROR for record in safe_events)
        assert any(
            "module=main function=_update_memory line=" in record.getMessage()
            for record in safe_events
        )
        assert all(record.exc_info is None and record.args == () for record in safe_events)
        assert private_text not in caplog.text
        assert private_error not in caplog.text
    finally:
        sdk.close()
        sdk.vector_store.client.close()
