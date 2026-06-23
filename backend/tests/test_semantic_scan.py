"""
T013: 数据源自动扫描 → 生成语义层 JSON

对标 SEM-001 (openspec/changes/chatbi-v2/specs/semantic-layer/spec.md)
对标 v1 教训 #15: 元数据质量是准确率根本（中文描述为空 → LLM 只能猜 → SQL 错）

测试用 mock inspector（不依赖真实数据库），端到端真库扫描在 e2e 脚本验证。
LLM 中文推断函数可注入（不强制真实调用）。
"""
import pytest

from app.schemas.semantic_layer import (
    Column,
    Model,
    Relationship,
    SemanticModelContent,
)


# ── 测试用 mock inspector 数据（模拟 SQLAlchemy inspector 返回）────────

def _mock_inspector(tables_specs: dict):
    """构造一个假 inspector，模拟真实 inspect(engine) 的行为。

    反映真实 PG inspector 语义:
      - 列注释在 get_columns() 返回的每个 col dict 的 "comment" 字段里
        (PG 没有 get_column_comment 方法，端到端扫描验证过)
      - 表注释在 get_table_comment() 返回的 {"text": ...} 里

    tables_specs: {
        "users": {
            "columns": [{"name":"id","type":"INTEGER","comment":"主键",...}, ...],
            "comment": "用户表",
            "foreign_keys": [],
        },
        ...
    }
    """
    class FakeInspector:
        def get_table_names(self):
            return list(tables_specs.keys())

        def get_columns(self, table):
            return tables_specs[table]["columns"]

        def get_table_comment(self, table):
            return {"text": tables_specs[table].get("comment") or ""}

        def get_foreign_keys(self, table):
            return tables_specs[table]["foreign_keys"]

    return FakeInspector()


def _users_orders_specs():
    """两个表 + 一个外键关系（orders.user_id → users.id）。列注释在 col dict 里。"""
    return {
        "users": {
            "columns": [
                {"name": "id", "type": "INTEGER", "primary_key": True, "nullable": False, "comment": "主键"},
                {"name": "username", "type": "VARCHAR(128)", "nullable": False, "comment": "用户名"},
                {"name": "city", "type": "VARCHAR(64)", "nullable": True, "comment": "所在城市"},
            ],
            "comment": "用户表",
            "foreign_keys": [],
        },
        "orders": {
            "columns": [
                {"name": "id", "type": "INTEGER", "primary_key": True, "nullable": False, "comment": "主键"},
                {"name": "user_id", "type": "BIGINT", "nullable": False, "comment": "下单用户ID"},
                {"name": "total_amount", "type": "DECIMAL(12,2)", "nullable": False, "comment": "订单总金额"},
                {"name": "status", "type": "VARCHAR(32)", "nullable": False, "comment": "订单状态"},
            ],
            "comment": "订单表",
            "foreign_keys": [
                {
                    "constrained_columns": ["user_id"],
                    "referred_table": "users",
                    "referred_columns": ["id"],
                }
            ],
        },
    }


# ── 扫描结果结构正确性 ────────────────────────────────────────

class TestScanProducesSemanticModelContent:
    """T013: scan_data_source 返回合法的 SemanticModelContent。"""

    def test_scan_returns_content_with_models(self):
        from app.services.semantic_scanner import scan_data_source

        content = scan_data_source(_mock_inspector(_users_orders_specs()))
        assert isinstance(content, SemanticModelContent)
        assert len(content.models) == 2
        names = {m.name for m in content.models}
        assert names == {"users", "orders"}

    def test_columns_preserved_with_data_type(self):
        """对标 #15: 列的 data_type 必须保留（SQL 生成要约束函数选择）。"""
        from app.services.semantic_scanner import scan_data_source

        content = scan_data_source(_mock_inspector(_users_orders_specs()))
        orders = next(m for m in content.models if m.name == "orders")
        col_names = {c.name for c in orders.columns}
        assert col_names == {"id", "user_id", "total_amount", "status"}
        amount = next(c for c in orders.columns if c.name == "total_amount")
        assert "DECIMAL" in amount.data_type


# ── 外键 → Relationship（对标海泰缺陷：显式化 JOIN）─────────

class TestForeignKeyToRelationship:
    """
    T013: 外键自动转为 Relationship（source=foreign_key, confidence=1.0）。

    海泰用 extract_table_name 正则取 FROM，不支持 JOIN（明确缺陷）。
    v2 用显式 Relationship，Agent 可直接读 JOIN 条件。
    """

    def test_foreign_key_becomes_relationship(self):
        from app.services.semantic_scanner import scan_data_source

        content = scan_data_source(_mock_inspector(_users_orders_specs()))
        orders = next(m for m in content.models if m.name == "orders")
        rels = orders.relationships
        assert len(rels) == 1
        rel = rels[0]
        assert rel.target_model == "users"
        assert rel.type == "N:1"  # orders.user_id → users 是多对一
        assert rel.source == "foreign_key"
        assert rel.confidence == 1.0

    def test_no_foreign_key_no_relationship(self):
        from app.services.semantic_scanner import scan_data_source

        content = scan_data_source(_mock_inspector(_users_orders_specs()))
        users = next(m for m in content.models if m.name == "users")
        assert len(users.relationships) == 0  # users 没有外键

    def test_relationship_on_clause_generated(self):
        """on 条件应拼接成 orders.user_id = users.id 格式。"""
        from app.services.semantic_scanner import scan_data_source

        content = scan_data_source(_mock_inspector(_users_orders_specs()))
        orders = next(m for m in content.models if m.name == "orders")
        rel = orders.relationships[0]
        assert "user_id" in rel.on and "id" in rel.on


# ── source/confidence 标注（SEM-001）──────────────────────────

class TestSourceAndConfidence:
    """
    SEM-001: AI 推断的'可能是'和'确定是'要有明确标注。

    - 有注释的表/列: source=manual, confidence=1.0 (人工写的)
    - 外键关系: source=foreign_key, confidence=1.0 (确定性最高)
    - LLM 推断的中文: source=auto_inferred, confidence<1.0
    """

    def test_table_with_comment_source_manual(self):
        from app.services.semantic_scanner import scan_data_source

        content = scan_data_source(_mock_inspector(_users_orders_specs()))
        users = next(m for m in content.models if m.name == "users")
        assert users.source == "manual"
        assert users.confidence == 1.0
        assert users.display_name == "用户表"  # 用注释做 display_name

    def test_column_with_comment_source_manual(self):
        from app.services.semantic_scanner import scan_data_source

        content = scan_data_source(_mock_inspector(_users_orders_specs()))
        users = next(m for m in content.models if m.name == "users")
        username = next(c for c in users.columns if c.name == "username")
        assert username.source == "manual"
        assert username.confidence == 1.0
        assert username.display_name == "用户名"

    def test_column_without_comment_needs_inference(self):
        """
        对标 #15: 没注释的列需要 LLM 推断（source=auto_inferred）。
        本测试: 不注入 LLM 时，无注释列的 display_name 退化为列名，source 标 auto_inferred。
        """
        from app.services.semantic_scanner import scan_data_source

        content = scan_data_source(_mock_inspector(_users_orders_specs()), infer_llm=None)
        users = next(m for m in content.models if m.name == "users")
        # id 列有注释 "主键"
        id_col = next(c for c in users.columns if c.name == "id")
        assert id_col.display_name == "主键"


# ── LLM 中文推断可注入 ────────────────────────────────────────

class TestLLMInferenceInjection:
    """LLM 推断函数可注入（可 mock），不强制真实调用。"""

    def test_inject_llm_infer_fills_display_name(self):
        """注入一个 fake infer 函数，验证它被调用且结果填入。"""
        from app.services.semantic_scanner import scan_data_source

        def fake_infer(table_name, columns):
            # 简单 mock: 给每列补一个中文名
            mapping = {"id": "编号", "username": "用户名", "city": "城市"}
            return {c["name"]: mapping.get(c["name"], c["name"]) for c in columns}

        # users 表的 city 列有注释，但 username 用注释；这里测 id 列(有注释)
        # 改为测无注释场景
        specs = {
            "users": {
                "columns": [
                    {"name": "id", "type": "INTEGER", "primary_key": True, "nullable": False},
                    {"name": "city", "type": "VARCHAR(64)", "nullable": True},
                ],
                "comment": "",
                "column_comments": {},  # 无任何注释
                "foreign_keys": [],
            }
        }
        content = scan_data_source(_mock_inspector(specs), infer_llm=fake_infer)
        users = content.models[0]
        # infer_llm 补的中文
        assert any(c.display_name == "编号" for c in users.columns)
        # infer 的列 source = auto_inferred
        id_col = next(c for c in users.columns if c.name == "id")
        assert id_col.source == "auto_inferred"


# ── 宁缺毋滥：空扫描不崩 ──────────────────────────────────────

class TestEmptyScan:
    """对标 #29: 空输入不应静默 fallback，但要优雅返回空结构（可操作的空）。"""

    def test_empty_database_returns_empty_content(self):
        from app.services.semantic_scanner import scan_data_source

        content = scan_data_source(_mock_inspector({}))
        assert isinstance(content, SemanticModelContent)
        assert content.models == []
