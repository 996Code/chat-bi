"""
ChatBI v2 — 记忆种子脚本

根据数据源的实际表结构, 生成有针对性的测试记忆。
记忆按 tenant_id + data_source_id 隔离, 生成后 Agent 查询时自动召回。

用法:
  # 默认: 用 dev token + 第一个数据源
  python backend/scripts/seed_memories.py

  # 指定数据源 ID
  DATA_SOURCE_ID=ff580191db53408ea214da49d2632254 python backend/scripts/seed_memories.py

  # 指定后端地址
  API_BASE=http://localhost:8999/chat-bi/api/v1 python backend/scripts/seed_memories.py

  # 清空后重新生成
  CLEAN=true python backend/scripts/seed_memories.py

  # 只生成 linkage 记忆 (从语义层关系推断)
  LINKAGE_ONLY=true python backend/scripts/seed_memories.py

幂等: 已存在的同名记忆会被覆盖 (PUT 语义)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

# ── 配置 ──────────────────────────────────────────────────────
API_BASE = os.getenv("API_BASE", "http://localhost:8999/chat-bi/api/v1")
DATA_SOURCE_ID = os.getenv("DATA_SOURCE_ID", "")
CLEAN = os.getenv("CLEAN", "").lower() in ("1", "true", "yes")
LINKAGE_ONLY = os.getenv("LINKAGE_ONLY", "").lower() in ("1", "true", "yes")

# ── 电商场景记忆模板 ──────────────────────────────────────────
# 每条记忆都是 Agent 在实际查询中会遇到的业务约定/字段含义/常见陷阱
# type: reference = 事实约定, user = 用户偏好, project = 项目级约定

ECOM_MEMORIES: list[dict] = [
    # ── 1. GMV 计算规则 ──
    {
        "name": "gmv-calculation-rule",
        "description": "GMV 计算规则: 用 biz_orders.actual_amount 而非 total_amount",
        "type": "reference",
        "content": """\
# GMV 计算规则

**核心约定**: GMV = SUM(biz_orders.actual_amount)

- `actual_amount` = 用户实付金额 (扣除了优惠券、满减等折扣)
- `total_amount` = 订单原价 (未扣折扣), **不能**用来算 GMV
- `discount_amount` = 优惠金额 = total_amount - actual_amount
- `freight_amount` = 运费, 已包含在 actual_amount 中

常见错误:
- ❌ SUM(total_amount) — 包含未扣折扣的原价, 偏高
- ❌ SUM(amount) from fin_transactions — 交易流水含退款/佣金, 不等于 GMV
- ✅ SUM(actual_amount) from biz_orders WHERE status != 'pending' — 排除未支付

补充: 如需"含运费 GMV", 直接用 actual_amount (已含); 如需"不含运费 GMV", 用 actual_amount - freight_amount。""",
    },
    # ── 2. 订单状态流转 ──
    {
        "name": "order-status-flow",
        "description": "订单状态枚举及含义: pending→paid→shipped→received→completed",
        "type": "reference",
        "content": """\
# 订单状态流转

biz_orders.status 枚举值及含义:

| 状态 | 含义 | 是否已付款 |
|------|------|-----------|
| pending | 待付款 | ❌ |
| paid | 已付款待发货 | ✅ |
| shipped | 已发货待收货 | ✅ |
| received | 已收货 | ✅ |
| completed | 已完成 (可能已评价) | ✅ |

关键规则:
- "有效订单" = status IN ('paid','shipped','received','completed')
- "待处理订单" = status = 'pending'
- "退款中" 不在 biz_orders.status, 需关联 biz_order_refunds
- 退款单状态: pending(待审核) → approved(已通过) → completed(已退款) / rejected(已拒绝)

常见问题:
- 问"订单量"默认指有效订单, 不含 pending
- 问"完成率" = completed 数 / 有效订单数 (不含 pending)""",
    },
    # ── 3. 支付方式映射 ──
    {
        "name": "payment-method-mapping",
        "description": "支付方式字段值映射: wechat/alipay/card/balance",
        "type": "reference",
        "content": """\
# 支付方式映射

biz_orders.payment_method 枚举:

| 值 | 含义 |
|----|------|
| wechat | 微信支付 |
| alipay | 支付宝 |
| card | 银行卡 |
| balance | 余额支付 |
| (空字符串) | 未支付 (pending 状态) |

biz_order_payments.payment_method 同样使用上述枚举。

统计"支付方式占比"时:
- 排除 payment_method 为空的记录 (未支付)
- 按 payment_method 分组, COUNT 或 SUM(actual_amount)""",
    },
    # ── 4. 商品状态码 ──
    {
        "name": "product-status-codes",
        "description": "商品状态码: 0=下架 1=上架 2=草稿, is_on_sale 字段含义",
        "type": "reference",
        "content": """\
# 商品状态码

pd_products.status (smallint):

| 值 | 含义 |
|----|------|
| 0 | 已下架 |
| 1 | 已上架 |
| 2 | 草稿 (未发布) |

pd_products.is_on_sale (boolean):
- true = 正在售卖 (status=1)
- false = 未在售卖 (status=0 或 2)

关键区别:
- "在售商品" → status = 1 或 is_on_sale = true (等价)
- "全部商品" → 不过滤 status
- "已下架商品" → status = 0

pd_products.price vs pd_products.original_price:
- price = 现价 (售价)
- original_price = 原价 (划线价)
- 折扣率 = price / original_price""",
    },
    # ── 5. 表前缀分类 ──
    {
        "name": "table-prefix-convention",
        "description": "表名前缀分类: biz/pd/uc/fin/mkt/ops/st/cs/wms/sup/sys",
        "type": "reference",
        "content": """\
# 表名前缀分类

数据库共 121 张表, 按前缀分为 10 个业务域:

| 前缀 | 业务域 | 核心表 |
|------|--------|--------|
| biz_ | 交易订单 | biz_orders, biz_order_items, biz_order_payments, biz_order_refunds |
| pd_ | 商品 | pd_products, pd_product_skus, pd_categories, pd_brands |
| uc_ | 用户 | uc_users, uc_addresses, uc_user_profiles, uc_points_accounts |
| fin_ | 财务 | fin_transactions, fin_settlements, fin_invoices |
| mkt_ | 营销 | mkt_promotions, mkt_flash_sales, mkt_group_buys, mkt_coupons |
| ops_ | 运营 | ops_daily_reports, ops_product_daily_reports, ops_user_funnel_logs |
| st_ | 店铺 | st_shops, st_shop_categories, st_shop_rates |
| cs_ | 客服 | cs_service_tickets, cs_after_sales, cs_complaints |
| wms_ | 仓储 | wms_warehouses, wms_inventory_records, wms_outbound_orders |
| sup_ | 供应 | sup_suppliers, sup_purchase_orders |
| sys_ | 系统 | sys_admins, sys_operation_logs (一般不查) |

用户说"订单"→ biz_orders, "商品"→ pd_products, "用户"→ uc_users
用户说"营收/收入"→ fin_transactions 或 biz_orders.actual_amount
用户说"库存"→ wms_inventory_records 或 pd_sku_stocks""",
    },
    # ── 6. 退款与净收入 ──
    {
        "name": "refund-and-net-revenue",
        "description": "退款计算: biz_order_refunds 关联 biz_orders, 净收入 = GMV - 退款",
        "type": "reference",
        "content": """\
# 退款与净收入计算

退款表: biz_order_refunds
- 通过 order_id 关联 biz_orders
- total_amount = 退款金额
- status: pending/approved/completed/rejected
- refund_type: 退款类型 (如"仅退款"/"退货退款")

净收入计算:
- 净收入 = SUM(actual_amount) - SUM(退款金额)
- 退款金额只算 status IN ('approved','completed') 的
- pending 退款不算 (可能被拒绝)

SQL 模式:
```sql
SELECT
  SUM(o.actual_amount) AS gmv,
  COALESCE(SUM(r.total_amount) FILTER (WHERE r.status IN ('approved','completed')), 0) AS refund_amount,
  SUM(o.actual_amount) - COALESCE(SUM(r.total_amount) FILTER (WHERE r.status IN ('approved','completed')), 0) AS net_revenue
FROM biz_orders o
LEFT JOIN biz_order_refunds r ON r.order_id = o.id
WHERE o.status != 'pending'
```""",
    },
    # ── 7. 运营日报指标 ──
    {
        "name": "ops-daily-report-metrics",
        "description": "ops_daily_reports 核心指标: DAU/新增/订单量/客单价/转化率/退款率",
        "type": "reference",
        "content": """\
# 运营日报指标

ops_daily_reports 每日汇总, 字段含义:

| 字段 | 含义 | 单位 |
|------|------|------|
| report_date | 报告日期 | date |
| total_users | 累计用户数 | 人 |
| new_users | 当日新增用户 | 人 |
| active_users | 当日活跃用户 (DAU) | 人 |
| total_orders | 当日订单量 | 单量 | 笔 |
| order_amount | 当日订单金额 | 元 |
| avg_order_amount | 客单价 | 元 |
| refund_rate | 退款率 | 小数 (0.05 = 5%) |
| conversion_rate | 转化率 | 小数 (0.03 = 3%) |

常见查询:
- "DAU 趋势" → active_users 按 report_date
- "客单价" → avg_order_amount (已算好, 不需要自己除)
- "转化率" → conversion_rate (已算好)
- "月度汇总" → 按 DATE_TRUNC('month', report_date) 分组聚合""",
    },
    # ── 8. 用户偏好: 时间维度 ──
    {
        "name": "user-time-preference",
        "description": "用户查询偏好: 默认按月维度, 金额单位用万元",
        "type": "user",
        "content": """\
# 用户查询偏好

时间维度:
- 用户说"最近"默认指最近 30 天
- 用户说"本月/这个月"用 DATE_TRUNC('month', CURRENT_DATE)
- 用户说"同期/同比"需要去年同期对比
- 日期字段统一用 created_at, 不用 updated_at

金额展示:
- 金额超过 10000 时, 自动除以 10000 显示为"万元"
- 图表 Y 轴标签加"万元"后缀
- SQL 中仍用原始值计算, 只在展示层转换

排序:
- 排行榜默认 TOP 10
- 趋势图默认按时间升序""",
    },
    # ── 9. 营销活动类型 ──
    {
        "name": "promotion-types",
        "description": "营销活动类型: general/flash/group_buy/full_reduction",
        "type": "reference",
        "content": """\
# 营销活动类型

mkt_promotions.promotion_type 枚举:

| 值 | 含义 | 说明 |
|----|------|------|
| general | 通用促销 | 普通折扣/满减活动 |
| flash | 限时秒杀 | mkt_flash_sales 有详细配置 |
| group_buy | 拼团 | mkt_group_buys 有拼团价和成团人数 |
| full_reduction | 满减 | mkt_full_reductions 有阶梯规则 |

mkt_promotions.status (smallint):
- 0 = 未开始
- 1 = 进行中
- 2 = 已结束

查询"当前活动" → status = 1 AND CURRENT_TIMESTAMP BETWEEN start_at AND end_at
查询"活动效果" → 关联 biz_order_promotions (订单与活动的关联表)""",
    },
    # ── 10. 店铺与商品关联 ──
    {
        "name": "shop-product-relationship",
        "description": "店铺-商品-类目关联: st_shops ↔ pd_products ↔ pd_categories",
        "type": "reference",
        "content": """\
# 店铺-商品-类目关联

关联路径:
- st_shops.id → pd_products.shop_id (一个店铺多个商品)
- pd_products.category_id → pd_categories.id (一个商品一个类目)
- pd_products.brand_id → pd_brands.id (一个商品一个品牌)

st_shops 核心字段:
- monthly_sales / total_sales: 月销/总销量
- rating: 店铺评分
- commission_rate: 佣金比例 (小数, 0.05 = 5%)
- province / city: 店铺地区

pd_categories 是树形结构 (有 parent_id), 但当前数据只有一级类目。

常见查询:
- "各店铺销售额" → st_shops JOIN biz_orders ON shop_id
- "各类目商品数" → pd_products GROUP BY category_id JOIN pd_categories
- "品牌排行" → pd_products JOIN pd_brands, 按 sales_count 排序""",
    },
]


# ── 电商场景高频查询表对 + 典型场景 ──────────────────────────────
# 从语义层关系 + 常见业务查询场景推断, 生成带结构化 frontmatter 的 linkage 记忆
# co_occurrence 模拟真实查询频次, scenes 是典型业务问题, aggregation 是常见聚合方式

ECOM_LINKAGE_MEMORIES: list[dict] = [
    # ── 核心交易链路 (最高频) ──
    {
        "tables": ["biz_orders", "uc_users"],
        "co_occurrence": 8,
        "join_paths": [{"on": "biz_orders.user_id = uc_users.id", "join_type": "LEFT"}],
        "scenes": ["按用户分组统计订单", "查询每个用户的订单数", "用户消费排行", "VIP 用户订单分析"],
        "aggregation": "COUNT",
    },
    {
        "tables": ["biz_orders", "biz_order_items"],
        "co_occurrence": 7,
        "join_paths": [{"on": "biz_order_items.order_id = biz_orders.id", "join_type": "LEFT"}],
        "scenes": ["订单明细拆分", "每个订单的商品数", "订单金额与明细对比"],
        "aggregation": "SUM",
    },
    {
        "tables": ["biz_order_items", "pd_products"],
        "co_occurrence": 6,
        "join_paths": [{"on": "biz_order_items.product_id = pd_products.id", "join_type": "LEFT"}],
        "scenes": ["各品类销售额", "商品排行", "热销商品分析"],
        "aggregation": "SUM",
    },
    {
        "tables": ["biz_orders", "pd_products"],
        "co_occurrence": 5,
        "join_paths": [],  # 间接关联
        "scenes": ["促销活动订单", "商品销售趋势"],
        "aggregation": "SUM",
        "indirect": True,
        "via": "biz_order_items",
    },
    # ── 营销分析 ──
    {
        "tables": ["biz_orders", "mkt_promotions"],
        "co_occurrence": 6,
        "join_paths": [
            {"on": "biz_order_promotions.order_id = biz_orders.id", "join_type": "LEFT"},
            {"on": "biz_order_promotions.promotion_id = mkt_promotions.id", "join_type": "LEFT"},
        ],
        "scenes": ["促销活动订单", "活动效果分析", "限时秒杀订单量", "活动 ROI"],
        "aggregation": "SUM",
    },
    {
        "tables": ["mkt_promotions", "pd_products"],
        "co_occurrence": 4,
        "join_paths": [{"on": "mkt_promotion_products.promotion_id = mkt_promotions.id", "join_type": "LEFT"}, {"on": "mkt_promotion_products.product_id = pd_products.id", "join_type": "LEFT"}],
        "scenes": ["活动商品列表", "促销商品销量对比"],
        "aggregation": "COUNT",
    },
    # ── 退款售后 ──
    {
        "tables": ["biz_orders", "biz_order_refunds"],
        "co_occurrence": 5,
        "join_paths": [{"on": "biz_order_refunds.order_id = biz_orders.id", "join_type": "LEFT"}],
        "scenes": ["退款率统计", "退款金额分析", "净收入计算"],
        "aggregation": "SUM",
    },
    {
        "tables": ["biz_order_refunds", "uc_users"],
        "co_occurrence": 3,
        "join_paths": [{"on": "biz_order_refunds.user_id = uc_users.id", "join_type": "LEFT"}],
        "scenes": ["用户退款记录", "退款用户画像"],
        "aggregation": "COUNT",
    },
    # ── 商品维度 ──
    {
        "tables": ["pd_products", "pd_categories"],
        "co_occurrence": 5,
        "join_paths": [{"on": "pd_products.category_id = pd_categories.id", "join_type": "LEFT"}],
        "scenes": ["各品类商品数", "品类销售额", "类目分布"],
        "aggregation": "COUNT",
    },
    {
        "tables": ["pd_products", "pd_brands"],
        "co_occurrence": 3,
        "join_paths": [{"on": "pd_products.brand_id = pd_brands.id", "join_type": "LEFT"}],
        "scenes": ["品牌商品数", "品牌排行"],
        "aggregation": "COUNT",
    },
    {
        "tables": ["pd_products", "st_shops"],
        "co_occurrence": 4,
        "join_paths": [{"on": "pd_products.shop_id = st_shops.id", "join_type": "LEFT"}],
        "scenes": ["店铺商品列表", "各店铺商品数"],
        "aggregation": "COUNT",
    },
    # ── 支付 ──
    {
        "tables": ["biz_orders", "biz_order_payments"],
        "co_occurrence": 4,
        "join_paths": [{"on": "biz_order_payments.order_id = biz_orders.id", "join_type": "LEFT"}],
        "scenes": ["支付方式分布", "支付成功率", "各渠道支付金额"],
        "aggregation": "SUM",
    },
    # ── 店铺运营 ──
    {
        "tables": ["biz_orders", "st_shops"],
        "co_occurrence": 5,
        "join_paths": [{"on": "biz_orders.shop_id = st_shops.id", "join_type": "LEFT"}],
        "scenes": ["各店铺销售额", "店铺订单量排行", "店铺业绩对比"],
        "aggregation": "SUM",
    },
    {
        "tables": ["st_shops", "fin_settlements"],
        "co_occurrence": 3,
        "join_paths": [{"on": "fin_settlements.shop_id = st_shops.id", "join_type": "LEFT"}],
        "scenes": ["店铺结算金额", "待结算列表"],
        "aggregation": "SUM",
    },
    # ── 物流仓储 ──
    {
        "tables": ["biz_orders", "biz_order_logistics"],
        "co_occurrence": 3,
        "join_paths": [{"on": "biz_order_logistics.order_id = biz_orders.id", "join_type": "LEFT"}],
        "scenes": ["物流状态查询", "配送时效分析"],
        "aggregation": "COUNT",
    },
    {
        "tables": ["wms_inventory_records", "pd_product_skus"],
        "co_occurrence": 3,
        "join_paths": [{"on": "wms_inventory_records.sku_id = pd_product_skus.id", "join_type": "LEFT"}],
        "scenes": ["SKU 库存查询", "库存预警"],
        "aggregation": "SUM",
    },
    # ── 用户分析 ──
    {
        "tables": ["uc_users", "uc_user_profiles"],
        "co_occurrence": 3,
        "join_paths": [{"on": "uc_user_profiles.user_id = uc_users.id", "join_type": "LEFT"}],
        "scenes": ["用户画像", "用户详情查询"],
        "aggregation": "COUNT",
    },
    {
        "tables": ["uc_users", "uc_points_accounts"],
        "co_occurrence": 2,
        "join_paths": [{"on": "uc_points_accounts.user_id = uc_users.id", "join_type": "LEFT"}],
        "scenes": ["用户积分余额", "积分排行"],
        "aggregation": "SUM",
    },
    # ── 财务 ──
    {
        "tables": ["biz_orders", "fin_transactions"],
        "co_occurrence": 3,
        "join_paths": [],  # 间接关联
        "scenes": ["订单与交易流水对账", "财务收入分析"],
        "aggregation": "SUM",
        "indirect": True,
        "via": "st_shops",
    },
    # ── 运营日报 ──
    {
        "tables": ["ops_daily_reports", "ops_product_daily_reports"],
        "co_occurrence": 2,
        "join_paths": [],
        "scenes": ["运营日报汇总", "商品日报趋势"],
        "aggregation": "SUM",
        "indirect": True,
        "via": "report_date",
    },
]


def get_dev_token() -> str:
    """获取开发 token (需要 DEBUG=True)"""
    resp = httpx.post(
        f"{API_BASE}/dev/token",
        json={
            "tenant_id": "default_tenant",
            "user_id": "admin_user",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_first_datasource(token: str) -> str:
    """获取第一个数据源 ID"""
    resp = httpx.get(
        f"{API_BASE}/data-sources",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    sources = resp.json()
    if not sources:
        print("❌ 没有可用的数据源, 请先创建数据源")
        sys.exit(1)
    ds = sources[0]
    print(f"📦 使用数据源: {ds['name']} ({ds['id']})")
    return ds["id"]


def clean_memories(token: str, ds_id: str) -> None:
    """清空该数据源的所有记忆"""
    resp = httpx.get(
        f"{API_BASE}/memory",
        params={"data_source_id": ds_id, "include_consolidated": True},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    memories = resp.json()
    for m in memories:
        try:
            del_resp = httpx.delete(
                f"{API_BASE}/memory/{m['id']}",
                params={"data_source_id": ds_id},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            del_resp.raise_for_status()
            print(f"  🗑️  删除: {m['name']}")
        except httpx.HTTPStatusError as e:
            # 404 = 已删除, 不阻塞
            if e.response.status_code != 404:
                raise
            print(f"  ⏭️  跳过 (已删除): {m['name']}")
    if memories:
        print(f"  已清空 {len(memories)} 条记忆")


def seed_memories(token: str, ds_id: str, memories: list[dict]) -> None:
    """写入业务知识记忆 (reference/user/project 类型)"""
    for m in memories:
        resp = httpx.put(
            f"{API_BASE}/memory",
            params={"data_source_id": ds_id},
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": m["name"],
                "description": m["description"],
                "content": m["content"],
                "type": m.get("type", "project"),
            },
            timeout=10,
        )
        resp.raise_for_status()
        print(f"  ✅ {m['name']}: {m['description']}")
    print(f"\n🎉 共写入 {len(memories)} 条业务知识记忆到数据源 {ds_id}")


def _build_linkage_content(link: dict) -> str:
    """从 linkage 模板构建 Markdown body (给 LLM 看)"""
    parts = []
    table_a, table_b = link["tables"]
    is_indirect = link.get("indirect", False)

    # JOIN 路径
    join_paths = link.get("join_paths", [])
    direct_joins = [jp for jp in join_paths if jp.get("on")]
    if direct_joins:
        parts.append("## JOIN 路径\n" + "\n".join(jp["on"] for jp in direct_joins) + "\n")
    elif is_indirect:
        via = link.get("via", "其他表")
        parts.append(f"## 关联方式\n间接关联（经由 {via}）\n")

    # 典型场景
    scenes = link.get("scenes", [])
    if scenes:
        parts.append("## 典型场景\n" + "\n".join(f"- {s}" for s in scenes) + "\n")

    # 聚合方式
    agg = link.get("aggregation", "")
    if agg:
        parts.append(f"## 聚合方式\n{agg}\n")

    return "\n".join(parts)


def seed_linkage_memories(ds_id: str) -> None:
    """直接用 AgentMemoryStore 写入 linkage 记忆 (带结构化 frontmatter)。

    绕过 API (API 不支持 extra_metadata), 直接操作文件系统。
    """
    # 添加项目根目录到 sys.path, 以便 import app
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from app.core.agent_memory import AgentMemoryStore

    # 用绝对路径, 确保写入 backend/memory/ (与 API 一致)
    backend_root = str(Path(__file__).resolve().parent.parent)
    memory_dir = f"{backend_root}/memory/default_tenant/{ds_id}"
    store = AgentMemoryStore(base_dir=memory_dir)

    for link in ECOM_LINKAGE_MEMORIES:
        table_a, table_b = sorted(link["tables"])
        name = f"linkage-{table_a}-{table_b}"
        description = f"表 {table_a} 和 {table_b} 的共现经验"
        content = _build_linkage_content(link)

        # 检查是否已有同名 linkage (按表对查找)
        existing = store.get_linkage_memory(table_a, table_b)

        # 构建 extra_metadata (结构化 frontmatter)
        extra_metadata: dict = {
            "co_occurrence": link["co_occurrence"],
            "tables": sorted([table_a, table_b]),
        }
        join_paths = link.get("join_paths", [])
        if join_paths:
            extra_metadata["join_paths"] = join_paths
        scenes = link.get("scenes", [])
        if scenes:
            extra_metadata["scenes"] = scenes
        agg = link.get("aggregation", "")
        if agg:
            extra_metadata["aggregation"] = agg

        store.save_memory(
            name=name,
            description=description,
            content=content,
            memory_type="linkage",
            mem_id=existing["id"] if existing else None,
            extra_metadata=extra_metadata,
        )
        print(f"  ✅ {name}: co_occurrence={link['co_occurrence']}, "
              f"join_paths={len(join_paths)}, scenes={len(scenes)}, agg={agg or '-'}")

    print(f"\n🎉 共写入 {len(ECOM_LINKAGE_MEMORIES)} 条 linkage 记忆到 {memory_dir}")


def main() -> None:
    print("🔑 获取开发 token...")
    token = get_dev_token()

    ds_id = DATA_SOURCE_ID
    if not ds_id:
        print("📦 查找数据源...")
        ds_id = get_first_datasource(token)

    if CLEAN:
        print("🧹 清空已有记忆...")
        clean_memories(token, ds_id)

    if not LINKAGE_ONLY:
        print(f"\n📝 写入电商场景测试记忆 ({len(ECOM_MEMORIES)} 条)...")
        seed_memories(token, ds_id, ECOM_MEMORIES)

    print(f"\n📝 写入 linkage 记忆 ({len(ECOM_LINKAGE_MEMORIES)} 条, 从语义层关系推断)...")
    seed_linkage_memories(ds_id)

    # 验证
    print("\n🔍 验证...")
    resp = httpx.get(
        f"{API_BASE}/memory",
        params={"data_source_id": ds_id},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()
    print(f"  当前记忆数: {len(result)}")

    # 分类统计
    by_type: dict[str, int] = {}
    for m in result:
        t = m.get("type", "project")
        by_type[t] = by_type.get(t, 0) + 1
    for t, count in sorted(by_type.items()):
        print(f"  - {t}: {count} 条")

    # 展示 linkage 记忆的结构化字段
    linkage_mems = [m for m in result if m.get("type") == "linkage"]
    if linkage_mems:
        print(f"\n  📊 Linkage 记忆详情:")
        for m in linkage_mems[:5]:
            co = m.get("co_occurrence", "?")
            tables = m.get("tables", [])
            jp = m.get("join_paths", [])
            sc = m.get("scenes", [])
            agg = m.get("aggregation", "-")
            print(f"    {tables}: co={co}, join_paths={len(jp)}, scenes={len(sc)}, agg={agg}")
        if len(linkage_mems) > 5:
            print(f"    ... 还有 {len(linkage_mems) - 5} 条")


if __name__ == "__main__":
    main()
