"""
ChatBI v2 — Tests for Database Models (T004)
"""
import pytest
from app.db.models import (
    Tenant, User, DataSource, AuditLog,
    Conversation, SavedQuery, SemanticModel,
)


class TestDatabaseModels:
    """T004: Database models for all core entities."""

    @pytest.mark.asyncio
    async def test_create_tenant(self, db_session):
        tenant = Tenant(name="Test Corp")
        db_session.add(tenant)
        await db_session.commit()

        result = await db_session.get(Tenant, tenant.id)
        assert result is not None
        assert result.name == "Test Corp"
        assert result.is_active is True

    @pytest.mark.asyncio
    async def test_create_user_with_tenant(self, db_session):
        tenant = Tenant(name="Test Corp")
        db_session.add(tenant)
        await db_session.flush()

        from app.core.security import hash_password
        user = User(
            tenant_id=tenant.id,
            email="user@test.com",
            username="testuser",
            hashed_password=hash_password("password123"),
            role="admin",
        )
        db_session.add(user)
        await db_session.commit()

        result = await db_session.get(User, user.id)
        assert result.email == "user@test.com"
        assert result.role == "admin"
        assert result.tenant_id == tenant.id

    @pytest.mark.asyncio
    async def test_audit_log_covers_all_statuses(self, db_session):
        """对标 v1 经验教训 #41: 审计日志覆盖成功+失败+拒绝"""
        tenant = Tenant(name="Test Corp")
        db_session.add(tenant)
        await db_session.flush()

        for status in ["success", "fail", "denied"]:
            log = AuditLog(
                tenant_id=tenant.id,
                resource_type="query",
                action="execute_sql",
                status=status,
            )
            db_session.add(log)

        await db_session.commit()


class TestCheckpointer:
    """T009: Checkpointer — append-only JSONL session persistence."""

    def test_save_and_load_turns(self, tmp_path):
        from app.core.checkpointer import Checkpointer

        checkpointer = Checkpointer(base_dir=str(tmp_path / "checkpoints"))

        # Save turns
        checkpointer.save_turn(
            "tenant1", "conv1", 1,
            state={"current_sql": "SELECT 1"},
            messages=[{"role": "user", "content": "hello"}],
        )
        checkpointer.save_turn(
            "tenant1", "conv1", 2,
            state={"current_sql": "SELECT * FROM orders"},
            messages=[{"role": "assistant", "content": "结果"}],
        )

        # Load
        turns = checkpointer.load_conversation("tenant1", "conv1")
        assert len(turns) == 2
        assert turns[0]["turn"] == 1
        assert turns[1]["turn"] == 2
        assert turns[1]["state"]["current_sql"] == "SELECT * FROM orders"

    def test_get_last_state(self, tmp_path):
        from app.core.checkpointer import Checkpointer

        checkpointer = Checkpointer(base_dir=str(tmp_path / "checkpoints"))

        # No turns → None
        assert checkpointer.get_last_state("t1", "c1") is None

        # Save then get
        checkpointer.save_turn("t1", "c1", 1, {"key": "value"}, [])
        state = checkpointer.get_last_state("t1", "c1")
        assert state["key"] == "value"


class TestAgentMemory:
    """T010: Agent memory — file-based + MEMORY.md index."""

    def test_save_and_read_memory(self, tmp_path):
        from app.core.agent_memory import AgentMemoryStore

        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))

        store.save_memory(
            "test-memory", "A test fact",
            "This is the memory body.\n**Why:** testing.\n**How to apply:** in tests.",
            memory_type="project",
        )

        memories = store.list_memories()
        assert len(memories) == 1
        assert memories[0]["name"] == "test-memory"
        assert memories[0]["type"] == "project"

        content = store.read_memory("test-memory")
        assert "This is the memory body" in content
        assert "name: test-memory" in content

    def test_delete_memory(self, tmp_path):
        from app.core.agent_memory import AgentMemoryStore

        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        store.save_memory("del-me", "test", "body")
        assert len(store.list_memories()) == 1

        store.delete_memory("del-me")
        assert len(store.list_memories()) == 0

    def test_index_truncation_protection(self, tmp_path):
        """对标 Claude Code: 200行/25KB 截断保护"""
        from app.core.agent_memory import AgentMemoryStore

        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))

        # Add many memories
        for i in range(100):
            store.save_memory(f"mem-{i}", f"Description {i}", f"Content {i}")

        index = store.read_index()
        lines = index.split("\n")
        assert len(lines) <= 200


class TestPromptCache:
    """T011: Prompt layered cache — static/dynamic boundary."""

    def test_static_caching(self):
        from app.core.prompt_cache import PromptCache

        call_count = 0

        def static_compute():
            nonlocal call_count
            call_count += 1
            return "static content"

        def dynamic_compute():
            return "dynamic content"

        cache = PromptCache(ttl_seconds=300)
        cache.add_static("test_static", static_compute)
        cache.add_dynamic("test_dynamic", dynamic_compute)

        # First assembly → computes both
        sections = cache.assemble()
        assert call_count == 1
        assert "static content" in "".join(sections)
        assert "dynamic content" in "".join(sections)
        assert "PROMPT_DYNAMIC_BOUNDARY" in "".join(sections)

        # Second assembly → static should be cached
        sections2 = cache.assemble()
        assert call_count == 1  # Still 1, not recomputed

    def test_invalidation(self):
        from app.core.prompt_cache import PromptCache

        call_count = 0

        def static_compute():
            nonlocal call_count
            call_count += 1
            return f"static v{call_count}"

        cache = PromptCache(ttl_seconds=300)
        cache.add_static("test", static_compute)

        assert call_count == 0
        sections = cache.assemble()
        assert "static v1" in "".join(sections)

        # Invalidate and reassemble
        cache.invalidate_static()
        sections2 = cache.assemble()
        assert "static v2" in "".join(sections2)
        assert call_count == 2
