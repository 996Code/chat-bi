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
        del_resp = httpx.delete(
            f"{API_BASE}/memory/{m['id']}",
            params={"data_source_id": ds_id},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        del_resp.raise_for_status()
        print(f"  🗑️  删除: {m['name']}")
    if memories:
        print(f"  已清空 {len(memories)} 条记忆")


def seed_memories(token: str, ds_id: str, memories: list[dict]) -> None:
    """写入记忆"""
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
    print(f"\n🎉 共写入 {len(memories)} 条记忆到数据源 {ds_id}")


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

    print(f"\n📝 写入电商场景测试记忆 ({len(ECOM_MEMORIES)} 条)...")
    seed_memories(token, ds_id, ECOM_MEMORIES)

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
    for m in result:
        print(f"  - [{m.get('type', '?')}] {m['name']}: {m['description']}")


if __name__ == "__main__":
    main()
