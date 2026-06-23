"""
T012: 语义层 JSON Schema 定义 — Pydantic v2 模型

对标 SEM-002 (openspec/changes/chatbi-v2/specs/semantic-layer/spec.md)
测试黄金样本取自 SEM-002 的 JSON 示例 (orders/gmv/客单价/relationship)。
"""
import pytest
from pydantic import ValidationError


# ── 黄金样本（取自 SEM-002 spec）──────────────────────────────

ORDERS_MODEL_JSON = {
    "name": "orders",
    "display_name": "订单表",
    "description": "存储所有订单信息",
    "source": "auto_inferred",
    "confidence": 0.85,
    "columns": [{
        "name": "total_amount",
        "display_name": "订单金额",
        "data_type": "DECIMAL(10,2)",
        "semantic_type": "measure",
        "description": "订单总金额（含税）",
        "source": "auto_inferred",
    }],
    "relationships": [{
        "name": "orders_to_users",
        "target_model": "users",
        "join_type": "LEFT",
        "on": "orders.user_id = users.id",
        "type": "N:1",
        "source": "foreign_key",
        "confidence": 1.0,
    }],
    "metrics": [
        {
            "name": "order_count",
            "display_name": "订单数",
            "formula": "COUNT(id)",
            "description": "总订单数量",
        },
        {
            "name": "gmv",
            "display_name": "GMV",
            "formula": "SUM(total_amount)",
            "condition": "status IN ('paid', 'shipped')",
            "description": "已支付+已发货的订单金额总和",
            "type": "single",
        },
        {
            "name": "avg_order_value",
            "display_name": "客单价",
            "formula": "gmv / order_count",
            "type": "composite",
            "factor_metric_names": ["gmv", "order_count"],
        },
    ],
    "calculated_fields": [{
        "name": "avg_item_price",
        "display_name": "平均单价",
        "formula": "total_amount / item_count",
    }],
}


class TestSemanticModelContent:
    """T012: SemanticModelContent 能完整解析 SEM-002 示例。"""

    def test_parse_orders_model_from_spec(self):
        from app.schemas.semantic_layer import Model

        model = Model.model_validate(ORDERS_MODEL_JSON)
        assert model.name == "orders"
        assert model.display_name == "订单表"
        assert model.confidence == 0.85
        assert model.source == "auto_inferred"
        # 列
        assert len(model.columns) == 1
        assert model.columns[0].semantic_type == "measure"
        # 关系
        assert len(model.relationships) == 1
        rel = model.relationships[0]
        assert rel.target_model == "users"
        assert rel.join_type == "LEFT"
        assert rel.type == "N:1"
        assert rel.source == "foreign_key"
        assert rel.confidence == 1.0
        # 指标 (single + composite 都能解析)
        assert len(model.metrics) == 3
        names = {m.name for m in model.metrics}
        assert names == {"order_count", "gmv", "avg_order_value"}
        # 复合指标
        composite = next(m for m in model.metrics if m.name == "avg_order_value")
        assert composite.type == "composite"
        assert composite.factor_metric_names == ["gmv", "order_count"]
        # 计算字段
        assert len(model.calculated_fields) == 1

    def test_top_level_content_with_version_and_models(self):
        from app.schemas.semantic_layer import SemanticModelContent

        content = SemanticModelContent.model_validate({
            "version": 1,
            "models": [ORDERS_MODEL_JSON],
        })
        assert content.version == 1
        assert len(content.models) == 1
        assert content.models[0].name == "orders"


# ── confidence 校验（0-1）────────────────────────────────────

class TestConfidenceValidation:
    """confidence 必须在 [0, 1] 区间。"""

    def test_confidence_out_of_range_rejected(self):
        from app.schemas.semantic_layer import Model

        bad = {**ORDERS_MODEL_JSON, "confidence": 1.5}
        with pytest.raises(ValidationError):
            Model.model_validate(bad)

    def test_confidence_negative_rejected(self):
        from app.schemas.semantic_layer import Model

        bad = {**ORDERS_MODEL_JSON, "confidence": -0.1}
        with pytest.raises(ValidationError):
            Model.model_validate(bad)

    def test_relationship_confidence_out_of_range_rejected(self):
        from app.schemas.semantic_layer import Relationship

        bad = {
            "name": "r1", "target_model": "users", "join_type": "LEFT",
            "on": "a.id = b.id", "type": "N:1",
            "source": "foreign_key", "confidence": 2.0,
        }
        with pytest.raises(ValidationError):
            Relationship.model_validate(bad)


# ── 复合指标约束（SEM-005）───────────────────────────────────

class TestCompositeMetricConstraint:
    """SEM-005: composite 必须有 factor_metric_names；子指标只能 single。"""

    def test_composite_without_factor_metric_names_rejected(self):
        from app.schemas.semantic_layer import Metric

        bad = {
            "name": "aov", "display_name": "客单价",
            "formula": "gmv / order_count",
            "type": "composite",
            # 缺 factor_metric_names
        }
        with pytest.raises(ValidationError):
            Metric.model_validate(bad)

    def test_single_with_factor_metric_names_ok(self):
        """single 类型有 factor_metric_names 不强制报错，但应保持语义干净。"""
        from app.schemas.semantic_layer import Metric

        m = Metric.model_validate({
            "name": "gmv", "display_name": "GMV",
            "formula": "SUM(total_amount)",
            "type": "single",
        })
        assert m.type == "single"
        assert m.factor_metric_names is None

    def test_composite_with_factor_metric_names_ok(self):
        from app.schemas.semantic_layer import Metric

        m = Metric.model_validate({
            "name": "aov", "display_name": "客单价",
            "formula": "gmv / order_count",
            "type": "composite",
            "factor_metric_names": ["gmv", "order_count"],
        })
        assert m.type == "composite"
        assert m.factor_metric_names == ["gmv", "order_count"]

    def test_metric_type_defaults_to_single(self):
        from app.schemas.semantic_layer import Metric

        m = Metric.model_validate({
            "name": "order_count", "display_name": "订单数",
            "formula": "COUNT(id)",
        })
        assert m.type == "single"  # 缺省值


# ── JSON Schema 导出 ─────────────────────────────────────────

class TestJsonSchemaExport:
    """语义层 schema 可导出为 JSON Schema（供前端/外部校验）。"""

    def test_model_json_schema_exportable(self):
        from app.schemas.semantic_layer import SemanticModelContent

        schema = SemanticModelContent.model_json_schema()
        assert schema["type"] == "object"
        assert "models" in schema["properties"]
        assert "version" in schema["properties"]


# ── 必填字段缺失 ─────────────────────────────────────────────

class TestRequiredFields:
    """关键字段缺失应被拒绝（宁缺毋滥：语义层结构必须完整）。"""

    def test_model_without_name_rejected(self):
        from app.schemas.semantic_layer import Model

        bad = {k: v for k, v in ORDERS_MODEL_JSON.items() if k != "name"}
        with pytest.raises(ValidationError):
            Model.model_validate(bad)

    def test_column_without_data_type_rejected(self):
        from app.schemas.semantic_layer import Column

        bad = {
            "name": "x", "display_name": "X",
            "semantic_type": "measure",
        }
        with pytest.raises(ValidationError):
            Column.model_validate(bad)

    def test_relationship_without_on_rejected(self):
        from app.schemas.semantic_layer import Relationship

        bad = {
            "name": "r1", "target_model": "users", "join_type": "LEFT",
            "type": "N:1", "source": "foreign_key", "confidence": 1.0,
        }
        with pytest.raises(ValidationError):
            Relationship.model_validate(bad)
