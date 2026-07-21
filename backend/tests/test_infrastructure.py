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
    """T010: Agent memory — file-based + MEMORY.md index. UUID-based id."""

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
        # id 是 UUID (文件名 stem)
        mem_id = memories[0]["id"]
        assert mem_id  # 非空

        content = store.read_memory(mem_id)
        assert "This is the memory body" in content
        assert "name: test-memory" in content

    def test_delete_memory(self, tmp_path):
        from app.core.agent_memory import AgentMemoryStore

        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        store.save_memory("del-me", "test", "body")
        assert len(store.list_memories()) == 1

        mem_id = store.list_memories()[0]["id"]
        store.delete_memory(mem_id)
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

    def test_save_linkage_memory_with_extra_metadata(self, tmp_path):
        """E1 Task 1.1: linkage 记忆能写入 extra_metadata (co_occurrence/tables)。"""
        from app.core.agent_memory import AgentMemoryStore

        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        store.save_memory(
            "表共现 biz_orders↔biz_users", "表共现经验",
            "## JOIN 路径\nbiz_orders.user_id = biz_users.id\n",
            memory_type="linkage",
            extra_metadata={"co_occurrence": 3, "tables": ["biz_orders", "biz_users"]},
        )

        memories = store.list_memories()
        linkage = [m for m in memories if m["type"] == "linkage"]
        assert len(linkage) == 1
        assert linkage[0]["co_occurrence"] == 3
        assert linkage[0]["tables"] == ["biz_orders", "biz_users"]

    def test_get_linkage_memory_by_tables(self, tmp_path):
        """E1 Task 1.1: 按 metadata.tables 查询 linkage 记忆 (文件名是 UUID, 不能按名查)。"""
        from app.core.agent_memory import AgentMemoryStore

        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        # 创建两条 linkage 记忆 + 一条普通记忆
        store.save_memory("共现A↔B", "desc", "body", memory_type="linkage",
                          extra_metadata={"co_occurrence": 2, "tables": ["tableA", "tableB"]})
        store.save_memory("共现A↔C", "desc", "body", memory_type="linkage",
                          extra_metadata={"co_occurrence": 1, "tables": ["tableA", "tableC"]})
        store.save_memory("普通记忆", "desc", "body", memory_type="project")

        # 按表对查询 (字典序无关, helper 内部排序)
        found = store.get_linkage_memory("tableB", "tableA")  # 乱序传入
        assert found is not None
        assert found["tables"] == ["tableA", "tableB"]
        assert found["co_occurrence"] == 2

        # 不存在的表对
        assert store.get_linkage_memory("tableX", "tableY") is None

    def test_update_linkage_memory_co_occurrence(self, tmp_path):
        """E1 Task 1.1: 已存在的表对记忆, 更新时 co_occurrence 递增。"""
        from app.core.agent_memory import AgentMemoryStore

        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        store.save_memory("共现A↔B", "desc", "body", memory_type="linkage",
                          extra_metadata={"co_occurrence": 1, "tables": ["tableA", "tableB"]})

        # 更新 (带 mem_id)
        existing = store.get_linkage_memory("tableA", "tableB")
        store.save_memory("共现A↔B", "desc", "body updated", memory_type="linkage",
                          mem_id=existing["id"],
                          extra_metadata={"co_occurrence": 2, "tables": ["tableA", "tableB"]})

        updated = store.get_linkage_memory("tableA", "tableB")
        assert updated["co_occurrence"] == 2
        # 不应产生重复记录
        linkage_count = len([m for m in store.list_memories() if m["type"] == "linkage"])
        assert linkage_count == 1

    def test_linkage_memory_long_description_not_truncated(self, tmp_path):
        """W1 修复: 长 description (frontmatter >500 字符) 时 co_occurrence/tables 仍可读。"""
        from app.core.agent_memory import AgentMemoryStore

        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        long_desc = "x" * 600  # 超过旧的 [:500] 截断阈值
        store.save_memory("共现长描述", long_desc, "body", memory_type="linkage",
                          extra_metadata={"co_occurrence": 7, "tables": ["tA", "tB"]})

        found = store.get_linkage_memory("tA", "tB")
        assert found is not None, "长 description 导致 co_occurrence/tables 解析失败 (W1)"
        assert found["co_occurrence"] == 7
        assert found["tables"] == ["tA", "tB"]

    def test_linkage_memory_table_name_with_schema_prefix(self, tmp_path):
        """W2 修复: 表名含 schema 前缀 (public.orders) 或特殊字符不破坏 YAML。"""
        from app.core.agent_memory import AgentMemoryStore

        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        store.save_memory("共现schema", "desc", "body", memory_type="linkage",
                          extra_metadata={"co_occurrence": 1,
                                          "tables": ["public.orders", "public.users"]})

        found = store.get_linkage_memory("public.orders", "public.users")
        assert found is not None, "schema 前缀表名解析失败 (W2)"
        assert found["tables"] == ["public.orders", "public.users"]

    def test_linkage_memory_invalid_extra_metadata_key_rejected(self, tmp_path):
        """W3 修复: extra_metadata key 含特殊字符拒绝 (防御 frontmatter 注入)。"""
        from app.core.agent_memory import AgentMemoryStore
        import pytest

        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        with pytest.raises(ValueError, match="Invalid extra_metadata key"):
            store.save_memory("bad", "desc", "body", memory_type="linkage",
                              extra_metadata={"bad:key": 1})  # 含冒号

    def test_linkage_memory_co_occurrence_zero_and_large(self, tmp_path):
        """co_occurrence 边界值: 0 和大值都能正确存取。"""
        from app.core.agent_memory import AgentMemoryStore

        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        for co in [0, 99999]:
            store.save_memory(f"共现{co}", "desc", "body", memory_type="linkage",
                              extra_metadata={"co_occurrence": co, "tables": [f"t{co}_a", f"t{co}_b"]})
            found = store.get_linkage_memory(f"t{co}_a", f"t{co}_b")
            assert found["co_occurrence"] == co

    def test_linkage_memory_structured_join_paths(self, tmp_path):
        """结构化 frontmatter: join_paths (list of dicts) 正确存取。"""
        from app.core.agent_memory import AgentMemoryStore

        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        join_paths = [
            {"on": "biz_orders.user_id = biz_users.id", "join_type": "LEFT"},
        ]
        store.save_memory("共现orders↔users", "desc", "body", memory_type="linkage",
                          extra_metadata={
                              "co_occurrence": 3,
                              "tables": ["biz_orders", "biz_users"],
                              "join_paths": join_paths,
                          })

        found = store.get_linkage_memory("biz_orders", "biz_users")
        assert found is not None
        assert found["join_paths"] == join_paths

    def test_linkage_memory_structured_scenes(self, tmp_path):
        """结构化 frontmatter: scenes (list of strings) 正确存取。"""
        from app.core.agent_memory import AgentMemoryStore

        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        scenes = ["本月各品类销售额", "订单和用户关联查询"]
        store.save_memory("共现orders↔users", "desc", "body", memory_type="linkage",
                          extra_metadata={
                              "co_occurrence": 5,
                              "tables": ["biz_orders", "biz_users"],
                              "scenes": scenes,
                          })

        found = store.get_linkage_memory("biz_orders", "biz_users")
        assert found is not None
        assert found["scenes"] == scenes

    def test_linkage_memory_structured_aggregation(self, tmp_path):
        """结构化 frontmatter: aggregation (标量字符串) 正确存取。"""
        from app.core.agent_memory import AgentMemoryStore

        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        store.save_memory("共现orders↔users", "desc", "body", memory_type="linkage",
                          extra_metadata={
                              "co_occurrence": 2,
                              "tables": ["biz_orders", "biz_users"],
                              "aggregation": "SUM",
                          })

        found = store.get_linkage_memory("biz_orders", "biz_users")
        assert found is not None
        assert found["aggregation"] == "SUM"

    def test_linkage_memory_all_structured_fields(self, tmp_path):
        """结构化 frontmatter: join_paths + scenes + aggregation 同时存取。"""
        from app.core.agent_memory import AgentMemoryStore

        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        join_paths = [{"on": "biz_orders.user_id = biz_users.id", "join_type": "LEFT"}]
        scenes = ["本月各品类销售额"]
        store.save_memory("共现orders↔users", "desc", "body", memory_type="linkage",
                          extra_metadata={
                              "co_occurrence": 5,
                              "tables": ["biz_orders", "biz_users"],
                              "join_paths": join_paths,
                              "scenes": scenes,
                              "aggregation": "SUM",
                          })

        found = store.get_linkage_memory("biz_orders", "biz_users")
        assert found is not None
        assert found["co_occurrence"] == 5
        assert found["tables"] == ["biz_orders", "biz_users"]
        assert found["join_paths"] == join_paths
        assert found["scenes"] == scenes
        assert found["aggregation"] == "SUM"

    def test_linkage_memory_backward_compat_no_structured_fields(self, tmp_path):
        """向后兼容: 旧格式 linkage 记忆 (无 join_paths/scenes/aggregation) 不报错。"""
        from app.core.agent_memory import AgentMemoryStore

        store = AgentMemoryStore(base_dir=str(tmp_path / "memory"))
        # 只写 co_occurrence + tables (旧格式)
        store.save_memory("共现orders↔users", "desc", "body", memory_type="linkage",
                          extra_metadata={"co_occurrence": 3, "tables": ["biz_orders", "biz_users"]})

        found = store.get_linkage_memory("biz_orders", "biz_users")
        assert found is not None
        assert found["co_occurrence"] == 3
        assert found["tables"] == ["biz_orders", "biz_users"]
        # 旧格式无结构化字段, 不应报错
        assert "join_paths" not in found
        assert "scenes" not in found
        assert "aggregation" not in found



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
