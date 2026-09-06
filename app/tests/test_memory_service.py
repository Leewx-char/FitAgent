"""Memory policy tests with no SQL memory table or external network."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models import Message
from app.services.memory_service import MemoryService
from app.tests.memory_fakes import FakeMemoryBackend


@pytest.fixture
def backend():
    return FakeMemoryBackend()


@pytest.fixture
def service(backend):
    return MemoryService(
        backend=backend,
        settings=SimpleNamespace(
            memory_enabled=True,
            memory_top_k=6,
            memory_context_max_chars=2400,
            memory_score_threshold=0.2,
            memory_default_ttl_days=90,
        ),
    )


def record(backend, text="偏好晚间训练", **metadata):
    return backend.create(
        user_id=metadata.pop("user_id", 1), text=text, metadata={"status": "confirmed", **metadata}
    )


def test_llm_candidates_live_in_backend_and_require_confirmation(service, backend):
    backend.extracted = ["preference: 只在午休时练习", "city: 杭州"]
    rows = service.extract_candidates(Message(id=10, role="user", content="午间才有空"), user_id=1)
    assert len(rows) == 2
    assert all(r.metadata["status"] == "proposed" for r in rows)
    assert "没有" in service.format_relevant_memories(user_id=1, query="什么时候合适")
    confirmed = service.update_memory(user_id=1, memory_id=rows[0].id, status="confirmed")
    assert confirmed.metadata["status"] == "confirmed"
    assert "午休" in service.format_relevant_memories(user_id=1, query="什么时候合适")


def test_assistant_never_reaches_extractor(service, backend):
    backend.extracted = ["city: 成都"]
    assert (
        service.extract_candidates(Message(id=11, role="assistant", content="你住成都"), user_id=1)
        == []
    )
    assert backend.extraction_inputs == []


def test_retry_does_not_resurrect_revoked_candidate(service, backend):
    backend.extracted = ["city: 杭州"]
    message = Message(id=10, role="user", content="我在杭州")
    row = service.extract_candidates(message, user_id=1)[0]
    service.update_memory(user_id=1, memory_id=row.id, status="revoked")
    service.extract_candidates(message, user_id=1)
    assert len(backend.entries) == 1
    assert service.list_for_user(user_id=1) == []


@pytest.mark.parametrize(
    "invalid", ["proposed", "revoked", "expired", "other_user", "unknown_status", "bad_date"]
)
def test_policy_vetoes_ineligible_hits_even_if_provider_returns_them(service, backend, invalid):
    row = record(backend)
    backend.hits = [row]
    if invalid in {"proposed", "revoked"}:
        row.metadata["status"] = invalid
    elif invalid == "expired":
        row.metadata["expires_at"] = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        ).isoformat()
    elif invalid == "other_user":
        row.user_id = 2
    elif invalid == "bad_date":
        row.metadata["expires_at"] = "invalid-date"
    else:
        row.metadata["status"] = "untrusted"
    result = service.format_relevant_memories(user_id=1, query="怎么安排")
    assert "没有" in result
    assert row.text not in result


def test_search_rechecks_current_status_and_text(service, backend):
    row = record(backend)
    stale = SimpleNamespace(**vars(row))
    stale.metadata = dict(row.metadata)
    backend.hits = [stale]
    service.update_memory(
        user_id=1, memory_id=row.id, status="confirmed", display_text="偏好清晨训练"
    )
    result = service.format_relevant_memories(user_id=1, query="什么时间合适")
    assert "清晨" in result and "晚间" not in result
    service.update_memory(user_id=1, memory_id=row.id, status="revoked")
    assert "没有" in service.format_relevant_memories(user_id=1, query="什么时间合适")


def test_relevance_order_duplicates_threshold_and_context_budget(service, backend):
    first = record(backend, "更相关的长期偏好")
    second = record(backend, "新近但不太相关的偏好")
    backend.hits = [first, first, second]
    result = service.format_relevant_memories(user_id=1, query="怎么安排")
    assert result.index(first.text) < result.index(second.text)
    assert result.count(first.text) == 1
    first.score = 0.01
    assert first.text not in service.format_relevant_memories(user_id=1, query="安排")
    service.settings.memory_context_max_chars = 100
    assert len(service.format_relevant_memories(user_id=1, query="怎么安排")) <= 100


def test_wrong_owner_cannot_update_and_revoked_cannot_be_reconfirmed(service, backend):
    row = record(backend)
    with pytest.raises(LookupError):
        service.update_memory(user_id=2, memory_id=row.id, status="revoked")
    assert row.metadata["status"] == "confirmed"
    service.update_memory(user_id=1, memory_id=row.id, status="revoked")
    with pytest.raises(ValueError):
        service.update_memory(user_id=1, memory_id=row.id, status="confirmed")


def test_provider_failure_is_not_reported_as_no_memories(service, backend):
    backend.fail = True
    assert (
        service.extract_candidates(Message(id=13, role="user", content="我在上海"), user_id=1) == []
    )
    result = service.format_relevant_memories(user_id=1, query="时间安排")
    assert "暂时不可用" in result and "private" not in result


def test_expiry_can_be_cleared_and_metadata_does_not_keep_stale_value(service, backend):
    row = record(backend, "城市杭州", value={"value": "杭州"}, expires_at="2020-01-01T00:00:00")
    updated = service.update_memory(
        user_id=1, memory_id=row.id, status="confirmed", display_text="城市成都", expires_at=None
    )
    assert updated.metadata["expires_at"] is None
    assert updated.metadata["value"] == {"value": "城市成都"}
    assert "成都" in service.format_relevant_memories(user_id=1, query="所在地")


def test_concurrent_confirmation_cannot_overwrite_completed_revocation(service, backend):
    """模拟确认读完后暂停；另一请求撤销时不得被旧状态覆盖。"""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from copy import deepcopy
    from threading import Event, current_thread

    row = record(backend)
    read_finished, release_confirmation, revoke_started = Event(), Event(), Event()
    original_get = backend.get

    def paused_get(**kwargs):
        if current_thread().name.endswith("_0") and not read_finished.is_set():
            snapshot = deepcopy(original_get(**kwargs))
            read_finished.set()
            assert release_confirmation.wait(5)
            return snapshot
        return original_get(**kwargs)

    backend.get = paused_get
    other_service = MemoryService(backend=backend, settings=service.settings)

    def revoke():
        revoke_started.set()
        return other_service.update_memory(user_id=1, memory_id=row.id, status="revoked")

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="memory-race") as executor:
        confirmation = executor.submit(
            service.update_memory, user_id=1, memory_id=row.id, status="confirmed"
        )
        assert read_finished.wait(5)
        revocation = executor.submit(revoke)
        assert revoke_started.wait(5)
        try:
            revocation.result(timeout=0.2)
        except TimeoutError:
            pass  # Serialized updates wait until the first mutation completes.
        finally:
            release_confirmation.set()
        confirmation.result(timeout=5)
        revocation.result(timeout=5)
    assert original_get(user_id=1, memory_id=row.id).metadata["status"] == "revoked"
