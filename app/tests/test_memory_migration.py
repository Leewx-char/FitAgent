"""旧 MySQL 记忆迁移的离线测试；源记录始终保留。"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import MemoryFact
from app.tests.memory_fakes import FakeMemoryBackend


@pytest.fixture
def legacy_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(
            [
                MemoryFact(
                    id="a" * 32,
                    user_id=1,
                    fact_key="city",
                    category="location",
                    display_text="城市杭州",
                    value='{"value":"杭州"}',
                    status="confirmed",
                    expires_at=datetime(2030, 1, 1),
                ),
                MemoryFact(
                    id="b" * 32,
                    user_id=1,
                    fact_key="diet_pref",
                    category="diet",
                    display_text="旧饮食偏好",
                    value="{}",
                    status="revoked",
                ),
                MemoryFact(
                    id="c" * 32,
                    user_id=2,
                    fact_key="custom",
                    category="custom",
                    display_text="他人的候选",
                    value="{}",
                    status="proposed",
                ),
            ]
        )
        db.commit()
        yield db
    engine.dispose()


def test_dry_run_leaves_both_stores_unchanged(legacy_db):
    from app.services.memory_migration import migrate_legacy_memories

    backend = FakeMemoryBackend()
    report = migrate_legacy_memories(legacy_db, backend=backend, user_id=1)
    assert report == {"selected": 2, "created": 0, "skipped": 0, "failed": 0}
    assert backend.entries == {}
    assert legacy_db.query(MemoryFact).count() == 3


def test_migration_preserves_states_and_is_repeatable(legacy_db):
    from app.services.memory_migration import migrate_legacy_memories

    backend = FakeMemoryBackend()
    first = migrate_legacy_memories(legacy_db, backend=backend, user_id=1, apply=True)
    second = migrate_legacy_memories(legacy_db, backend=backend, user_id=1, apply=True)
    assert first["created"] == 2 and second["skipped"] == 2
    rows = backend.list(user_id=1, include_revoked=True)
    assert {r.metadata["status"] for r in rows} == {"confirmed", "revoked"}
    assert {r.metadata["legacy_id"] for r in rows} == {"a" * 32, "b" * 32}
    assert all(r.user_id == 1 for r in rows)
    assert legacy_db.query(MemoryFact).count() == 3


def test_migration_failure_does_not_delete_source(legacy_db):
    from app.services.memory_migration import migrate_legacy_memories

    backend = FakeMemoryBackend()
    backend.fail = True
    result = migrate_legacy_memories(legacy_db, backend=backend, apply=True)
    assert result["failed"] == 3
    assert legacy_db.query(MemoryFact).count() == 3
