"""Integration checks for persistent memory using a disposable user namespace."""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.config import settings
from app.core.errors import MemoryStoreError
from app.core.interfaces import MemoryStore
from app.core.types import MemoryCandidate, MemoryTier
from app.memory import PostgresMemoryStore


class FakeEmbedder:
    """Stable vectors avoid requiring Ollama for database integration checks."""

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * settings.embedding.dim
        vector[0 if "中文" in text or "简短" in text else 1] = 1.0
        return vector


@pytest.fixture
def memory_store():
    user_id = f"memory-test-{uuid.uuid4().hex}"
    try:
        store = PostgresMemoryStore(embedder=FakeEmbedder())
        with store._connect():
            pass
    except MemoryStoreError as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")

    yield store, user_id

    with store._connect() as conn:
        conn.execute("DELETE FROM memories WHERE user_id = %s", (user_id,))
        conn.execute("DELETE FROM memories WHERE user_id = %s", (f"{user_id}-other",))


def candidate(tier: MemoryTier, content: str, importance: float = 0.5) -> MemoryCandidate:
    return MemoryCandidate(tier=tier, content=content, importance=importance)


def test_contract_crud_and_persistence(memory_store):
    store, user_id = memory_store
    assert isinstance(store, MemoryStore)

    created = store.add(candidate(MemoryTier.PREFERENCE, "偏好中文回答", 0.8), user_id)
    assert created.memory_id.startswith("mem-")
    assert created.user_id == user_id
    assert [item.memory_id for item in store.list_all(user_id)] == [created.memory_id]

    # A new instance represents a new application session reading persisted data.
    reopened = PostgresMemoryStore(embedder=FakeEmbedder())
    assert [item.memory_id for item in reopened.search("中文回答", user_id)] == [created.memory_id]

    changed = reopened.update(created.memory_id, content="请用中文简短回答", importance=0.9)
    assert changed.content == "请用中文简短回答"
    assert changed.importance == 0.9
    assert reopened.update("missing-memory-id", content="无") is None

    assert reopened.delete(created.memory_id) is True
    assert reopened.delete(created.memory_id) is False
    assert reopened.search("简短回答", user_id) == []


def test_user_and_tier_isolation(memory_store):
    store, user_id = memory_store
    own = store.add(candidate(MemoryTier.PROFILE, "正在学中文"), user_id)
    store.add(candidate(MemoryTier.PREFERENCE, "偏好中文回答"), f"{user_id}-other")

    assert [m.memory_id for m in store.list_all(user_id)] == [own.memory_id]
    assert [m.memory_id for m in store.search("中文", user_id)] == [own.memory_id]
    assert store.search("中文", user_id, tiers=[MemoryTier.TASK_STATE]) == []
    assert store.search("中文", user_id, tiers=[]) == []
    assert store.search("中文", user_id, top_k=0) == []
    assert store.search("unrelated topic", user_id) == []


def test_semantic_recency_importance_ranking(memory_store):
    store, user_id = memory_store
    older = store.add(candidate(MemoryTier.PREFERENCE, "中文回答", 0.5), user_id)
    newer = store.add(candidate(MemoryTier.PREFERENCE, "中文简短回答", 0.9), user_id)

    with psycopg.connect(store.dsn) as conn:
        conn.execute(
            "UPDATE memories SET updated_at = NOW() - INTERVAL '180 days' WHERE memory_id = %s",
            (older.memory_id,),
        )

    result = store.search("中文简短回答", user_id, top_k=1)
    assert [item.memory_id for item in result] == [newer.memory_id]


def test_empty_user_search_does_not_call_embedding(memory_store):
    store, user_id = memory_store

    class FailingEmbedder:
        def embed(self, text: str) -> list[float]:
            raise AssertionError("embedding must not be called for an empty user")

    store.embedder = FailingEmbedder()
    assert store.search("请记住", user_id) == []
