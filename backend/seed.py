"""Seed script: populate the database with complex e-commerce test data."""
import asyncio
import uuid
import json
import random
import hashlib
from datetime import datetime, timezone, timedelta

import sys
sys.path.insert(0, ".")

from app.db.session import async_session_factory
from app.services.datasource_service import DataSourceService
from app.services.audit_service import log_action
from app.services.analytics_service import track_event
from app.schemas.datasource import DataSourceCreate
from app.core.security import hash_password
from app.db.models import (
    User, Tenant, SavedQuery, Feedback, AnalyticsEvent,
    MetadataConfig, AuditLog, Conversation,
)
from app.core.encryption import encrypt_value


def _now():
    return datetime.now(timezone.utc)


def _past(days=0, hours=0, minutes=0):
    return _now() - timedelta(days=days, hours=hours, minutes=minutes)


# ─── Deterministic UUID helpers (so data is reproducible) ───
def _uuid(name: str) -> uuid.UUID:
    return uuid.UUID(hashlib.md5(f"chatbi-seed-{name}".encode()).hexdigest()[:32], version=4)


async def seed():
    async with async_session_factory() as session:
        # ════════════════════════════════════════
        # 1. Tenants
        # ════════════════════════════════════════
        tenant_main = Tenant(id=_uuid("tenant-main"), name="杭州星辰科技有限公司")
        tenant_demo = Tenant(id=_uuid("tenant-demo"), name="Demo 演示租户")
        session.add_all([tenant_main, tenant_demo])
        await session.commit()
        await session.refresh(tenant_main)
        await session.refresh(tenant_demo)
        print(f"  Tenants: {tenant_main.name}, {tenant_demo.name}")

        # ════════════════════════════════════════
        # 2. Users (8 users across 2 tenants)
        # ════════════════════════════════════════
        password_hash = await hash_password("Test1234!")

        users_main = [
            User(id=_uuid("user-admin"), tenant_id=tenant_main.id, email="admin@chatbi.com", password_hash=password_hash, role="admin", email_verified=True, is_locked=False),
            User(id=_uuid("user-analyst"), tenant_id=tenant_main.id, email="analyst@chatbi.com", password_hash=password_hash, role="user", email_verified=True),
            User(id=_uuid("user-viewer"), tenant_id=tenant_main.id, email="viewer@chatbi.com", password_hash=password_hash, role="user", email_verified=True),
            User(id=_uuid("user-ops"), tenant_id=tenant_main.id, email="ops@chatbi.com", password_hash=password_hash, role="user", email_verified=True),
            User(id=_uuid("user-locked"), tenant_id=tenant_main.id, email="locked@chatbi.com", password_hash=password_hash, role="user", email_verified=False, is_locked=True, failed_login_attempts=5, lock_until=_now() + timedelta(hours=2)),
        ]
        users_demo = [
            User(id=_uuid("user-demo"), tenant_id=tenant_demo.id, email="demo@chatbi.com", password_hash=password_hash, role="admin", email_verified=True),
        ]
        session.add_all(users_main + users_demo)
        await session.commit()
        for u in users_main + users_demo:
            await session.refresh(u)

        admin, analyst, viewer, ops, locked_user = users_main
        demo_admin = users_demo[0]
        print(f"  Users: 5 主租户 + 1 demo ({admin.email}, {analyst.email}, {viewer.email}, {ops.email}, {locked_user.email}[锁定])")

        # ════════════════════════════════════════
        # 3. Datasources
        # ════════════════════════════════════════
        ds_service = DataSourceService(session, tenant_id=str(tenant_main.id))
        ds_demo_service = DataSourceService(session, tenant_id=str(tenant_demo.id))

        ds_orders = await ds_service.create(DataSourceCreate(
            name="生产订单库 (MySQL)", type="mysql", host="192.168.1.100", port=3306,
            database_name="shop_production", username="chatbi_reader", password="R3ad!Only#2024",
        ))
        ds_analytics = await ds_service.create(DataSourceCreate(
            name="数据分析仓库 (PostgreSQL)", type="postgresql", host="10.0.0.50", port=5432,
            database_name="analytics_warehouse", username="bi_user", password="Bi$ecure2024",
        ))
        ds_demo = await ds_demo_service.create(DataSourceCreate(
            name="Demo 数据源", type="mysql", host="demo.example.com", port=3306,
            database_name="demo_db", username="root", password="root123",
        ))
        ds_demo.is_active = False
        ds_demo.health_check_error = "连接超时：主机 demo.example.com 不可达"
        ds_demo.last_health_check = _past(hours=3)

        # Broken datasource (simulates a misconfigured one)
        ds_broken = await ds_service.create(DataSourceCreate(
            name="测试库 (连接失败)", type="mysql", host="10.99.99.99", port=3306,
            database_name="test_db", username="test", password="test",
        ))
        ds_broken.is_active = False
        ds_broken.health_check_error = "Access denied for user 'test'@'10.0.0.1'"
        ds_broken.last_health_check = _past(hours=1)

        await session.commit()
        for ds in [ds_orders, ds_analytics, ds_demo, ds_broken]:
            await session.refresh(ds)
        print(f"  Datasources: 生产订单库(MySQL), 数据仓库(PG), Demo(异常), 测试库(连接失败)")

        # ════════════════════════════════════════
        # 4. Metadata Configs — Rich e-commerce schema
        # ════════════════════════════════════════

        # ── 4a. 生产订单库: 8 张表 + 关联关系 + 指标 ──
        metadata_orders = {
            "version": "1.1",
            "database": "shop_production",
            "models": [
                {
                    "name": "users", "alias": "用户表", "description": "平台注册用户基本信息，包含 VIP 等级和地域信息",
                    "columns": [
                        {"name": "id", "type": "BIGINT", "nullable": False, "primary": True, "alias": "用户ID", "comment": "自增主键"},
                        {"name": "username", "type": "VARCHAR(100)", "nullable": False, "alias": "用户名", "comment": ""},
                        {"name": "email", "type": "VARCHAR(255)", "nullable": False, "alias": "邮箱", "comment": ""},
                        {"name": "phone", "type": "VARCHAR(20)", "nullable": True, "alias": "手机号", "comment": ""},
                        {"name": "gender", "type": "TINYINT", "nullable": True, "alias": "性别", "comment": "0-未知 1-男 2-女"},
                        {"name": "city", "type": "VARCHAR(50)", "nullable": True, "alias": "城市", "comment": "常住城市"},
                        {"name": "province", "type": "VARCHAR(50)", "nullable": True, "alias": "省份", "comment": ""},
                        {"name": "vip_level", "type": "INT", "nullable": False, "alias": "VIP等级", "comment": "0-普通 1-铜 2-银 3-金 4-铂金 5-钻石"},
                        {"name": "vip_expire_date", "type": "DATE", "nullable": True, "alias": "VIP到期日", "comment": ""},
                        {"name": "total_spent", "type": "DECIMAL(12,2)", "nullable": False, "default": "0.00", "alias": "累计消费", "comment": "历史累计消费金额"},
                        {"name": "last_login_at", "type": "DATETIME", "nullable": True, "alias": "最后登录", "comment": ""},
                        {"name": "created_at", "type": "DATETIME", "nullable": False, "alias": "注册时间", "comment": ""},
                        {"name": "status", "type": "TINYINT", "nullable": False, "default": "1", "alias": "状态", "comment": "0-禁用 1-正常"},
                    ],
                },
                {
                    "name": "products", "alias": "商品表", "description": "平台在售商品目录，包含分类、价格、库存",
                    "columns": [
                        {"name": "id", "type": "BIGINT", "nullable": False, "primary": True, "alias": "商品ID", "comment": ""},
                        {"name": "name", "type": "VARCHAR(200)", "nullable": False, "alias": "商品名称", "comment": ""},
                        {"name": "category_id", "type": "BIGINT", "nullable": False, "alias": "分类ID", "comment": "关联 categories 表"},
                        {"name": "brand", "type": "VARCHAR(100)", "nullable": True, "alias": "品牌", "comment": ""},
                        {"name": "price", "type": "DECIMAL(10,2)", "nullable": False, "alias": "售价", "comment": "当前售价"},
                        {"name": "original_price", "type": "DECIMAL(10,2)", "nullable": True, "alias": "原价", "comment": "划线价"},
                        {"name": "cost_price", "type": "DECIMAL(10,2)", "nullable": True, "alias": "成本价", "comment": "仅供内部参考"},
                        {"name": "stock", "type": "INT", "nullable": False, "default": "0", "alias": "库存", "comment": "可售库存"},
                        {"name": "sales_count", "type": "INT", "nullable": False, "default": "0", "alias": "销量", "comment": "累计销量"},
                        {"name": "rating", "type": "DECIMAL(2,1)", "nullable": True, "alias": "评分", "comment": "平均评分 0-5"},
                        {"name": "is_on_sale", "type": "TINYINT", "nullable": False, "default": "1", "alias": "上架状态", "comment": "0-下架 1-上架"},
                        {"name": "created_at", "type": "DATETIME", "nullable": False, "alias": "上架时间", "comment": ""},
                    ],
                },
                {
                    "name": "categories", "alias": "商品分类表", "description": "商品多级分类",
                    "columns": [
                        {"name": "id", "type": "BIGINT", "nullable": False, "primary": True, "alias": "分类ID", "comment": ""},
                        {"name": "name", "type": "VARCHAR(100)", "nullable": False, "alias": "分类名称", "comment": ""},
                        {"name": "parent_id", "type": "BIGINT", "nullable": True, "alias": "父分类ID", "comment": "NULL 表示一级分类"},
                        {"name": "level", "type": "TINYINT", "nullable": False, "alias": "层级", "comment": "1-一级 2-二级 3-三级"},
                        {"name": "sort_order", "type": "INT", "nullable": False, "default": "0", "alias": "排序", "comment": ""},
                        {"name": "is_active", "type": "TINYINT", "nullable": False, "default": "1", "alias": "是否启用", "comment": ""},
                    ],
                },
                {
                    "name": "orders", "alias": "订单主表", "description": "用户下单的订单记录，一个订单可包含多个商品",
                    "columns": [
                        {"name": "id", "type": "BIGINT", "nullable": False, "primary": True, "alias": "订单ID", "comment": ""},
                        {"name": "order_no", "type": "VARCHAR(50)", "nullable": False, "unique": True, "alias": "订单编号", "comment": "业务订单号"},
                        {"name": "user_id", "type": "BIGINT", "nullable": False, "alias": "用户ID", "comment": "下单用户"},
                        {"name": "total_amount", "type": "DECIMAL(12,2)", "nullable": False, "alias": "订单总额", "comment": ""},
                        {"name": "discount_amount", "type": "DECIMAL(10,2)", "nullable": False, "default": "0.00", "alias": "优惠金额", "comment": ""},
                        {"name": "shipping_fee", "type": "DECIMAL(8,2)", "nullable": False, "default": "0.00", "alias": "运费", "comment": ""},
                        {"name": "pay_amount", "type": "DECIMAL(12,2)", "nullable": False, "alias": "实付金额", "comment": "total - discount + shipping"},
                        {"name": "pay_type", "type": "VARCHAR(20)", "nullable": True, "alias": "支付方式", "comment": "alipay/wechat/card/balance"},
                        {"name": "pay_time", "type": "DATETIME", "nullable": True, "alias": "支付时间", "comment": ""},
                        {"name": "status", "type": "VARCHAR(20)", "nullable": False, "alias": "订单状态", "comment": "pending/paid/shipping/delivered/completed/cancelled/refunded"},
                        {"name": "shipping_address", "type": "VARCHAR(500)", "nullable": True, "alias": "收货地址", "comment": ""},
                        {"name": "shipping_city", "type": "VARCHAR(50)", "nullable": True, "alias": "收货城市", "comment": ""},
                        {"name": "created_at", "type": "DATETIME", "nullable": False, "alias": "下单时间", "comment": ""},
                        {"name": "updated_at", "type": "DATETIME", "nullable": True, "alias": "更新时间", "comment": ""},
                    ],
                },
                {
                    "name": "order_items", "alias": "订单明细表", "description": "订单中包含的商品明细",
                    "columns": [
                        {"name": "id", "type": "BIGINT", "nullable": False, "primary": True, "alias": "明细ID", "comment": ""},
                        {"name": "order_id", "type": "BIGINT", "nullable": False, "alias": "订单ID", "comment": "关联 orders 表"},
                        {"name": "product_id", "type": "BIGINT", "nullable": False, "alias": "商品ID", "comment": "关联 products 表"},
                        {"name": "product_name", "type": "VARCHAR(200)", "nullable": False, "alias": "商品名称", "comment": "快照，防止商品改名"},
                        {"name": "unit_price", "type": "DECIMAL(10,2)", "nullable": False, "alias": "单价", "comment": "下单时单价"},
                        {"name": "quantity", "type": "INT", "nullable": False, "alias": "数量", "comment": ""},
                        {"name": "subtotal", "type": "DECIMAL(12,2)", "nullable": False, "alias": "小计", "comment": "unit_price * quantity"},
                    ],
                },
                {
                    "name": "coupons", "alias": "优惠券表", "description": "平台发放的优惠券/满减券",
                    "columns": [
                        {"name": "id", "type": "BIGINT", "nullable": False, "primary": True, "alias": "优惠券ID", "comment": ""},
                        {"name": "name", "type": "VARCHAR(100)", "nullable": False, "alias": "券名称", "comment": ""},
                        {"name": "type", "type": "VARCHAR(20)", "nullable": False, "alias": "类型", "comment": "discount-满减 percent-折扣 shipping-包邮"},
                        {"name": "value", "type": "DECIMAL(10,2)", "nullable": False, "alias": "面值/折扣值", "comment": "满减金额或折扣比例"},
                        {"name": "min_amount", "type": "DECIMAL(10,2)", "nullable": False, "default": "0", "alias": "最低消费", "comment": "满减门槛"},
                        {"name": "total_count", "type": "INT", "nullable": False, "alias": "发放总量", "comment": ""},
                        {"name": "used_count", "type": "INT", "nullable": False, "default": "0", "alias": "已使用", "comment": ""},
                        {"name": "start_date", "type": "DATE", "nullable": False, "alias": "生效日期", "comment": ""},
                        {"name": "end_date", "type": "DATE", "nullable": False, "alias": "失效日期", "comment": ""},
                        {"name": "is_active", "type": "TINYINT", "nullable": False, "default": "1", "alias": "是否有效", "comment": ""},
                    ],
                },
                {
                    "name": "user_coupons", "alias": "用户优惠券表", "description": "用户领取的优惠券",
                    "columns": [
                        {"name": "id", "type": "BIGINT", "nullable": False, "primary": True, "alias": "领取记录ID", "comment": ""},
                        {"name": "user_id", "type": "BIGINT", "nullable": False, "alias": "用户ID", "comment": ""},
                        {"name": "coupon_id", "type": "BIGINT", "nullable": False, "alias": "优惠券ID", "comment": ""},
                        {"name": "status", "type": "VARCHAR(20)", "nullable": False, "alias": "状态", "comment": "unused/used/expired"},
                        {"name": "used_at", "type": "DATETIME", "nullable": True, "alias": "使用时间", "comment": ""},
                        {"name": "order_id", "type": "BIGINT", "nullable": True, "alias": "使用订单", "comment": ""},
                        {"name": "received_at", "type": "DATETIME", "nullable": False, "alias": "领取时间", "comment": ""},
                    ],
                },
                {
                    "name": "refunds", "alias": "退款表", "description": "订单退款/退货记录",
                    "columns": [
                        {"name": "id", "type": "BIGINT", "nullable": False, "primary": True, "alias": "退款ID", "comment": ""},
                        {"name": "order_id", "type": "BIGINT", "nullable": False, "alias": "订单ID", "comment": ""},
                        {"name": "user_id", "type": "BIGINT", "nullable": False, "alias": "用户ID", "comment": ""},
                        {"name": "refund_amount", "type": "DECIMAL(12,2)", "nullable": False, "alias": "退款金额", "comment": ""},
                        {"name": "reason", "type": "VARCHAR(500)", "nullable": True, "alias": "退款原因", "comment": ""},
                        {"name": "status", "type": "VARCHAR(20)", "nullable": False, "alias": "状态", "comment": "pending/approved/rejected/completed"},
                        {"name": "created_at", "type": "DATETIME", "nullable": False, "alias": "申请时间", "comment": ""},
                        {"name": "processed_at", "type": "DATETIME", "nullable": True, "alias": "处理时间", "comment": ""},
                    ],
                },
            ],
            "relationships": [
                {"from_table": "orders", "from_column": "user_id", "to_table": "users", "to_column": "id"},
                {"from_table": "order_items", "from_column": "order_id", "to_table": "orders", "to_column": "id"},
                {"from_table": "order_items", "from_column": "product_id", "to_table": "products", "to_column": "id"},
                {"from_table": "products", "from_column": "category_id", "to_table": "categories", "to_column": "id"},
                {"from_table": "user_coupons", "from_column": "user_id", "to_table": "users", "to_column": "id"},
                {"from_table": "user_coupons", "from_column": "coupon_id", "to_table": "coupons", "to_column": "id"},
                {"from_table": "refunds", "from_column": "order_id", "to_table": "orders", "to_column": "id"},
                {"from_table": "refunds", "from_column": "user_id", "to_table": "users", "to_column": "id"},
            ],
            "metrics": [
                {"name": "GMV", "expression": "SUM(pay_amount)", "description": "成交总额（实付金额）"},
                {"name": "订单总数", "expression": "COUNT(DISTINCT id)", "description": "订单总数"},
                {"name": "客单价", "expression": "SUM(pay_amount) / COUNT(DISTINCT user_id)", "description": "每用户平均消费"},
                {"name": "退款率", "expression": "COUNT(DISTINCT CASE WHEN status='refunded' THEN id END) * 100.0 / COUNT(DISTINCT id)", "description": "退款订单占比"},
                {"name": "优惠券使用率", "expression": "SUM(CASE WHEN status='used' THEN 1 ELSE 0 END) * 100.0 / COUNT(*)", "description": "已领取优惠券的使用比例"},
                {"name": "平均订单金额", "expression": "AVG(pay_amount)", "description": "平均每单实付金额"},
            ],
        }

        # ── 4b. 数据分析仓库: 4 张表 ──
        metadata_analytics = {
            "version": "1.1",
            "database": "analytics_warehouse",
            "models": [
                {
                    "name": "daily_sales_summary", "alias": "日销售汇总", "description": "每日各维度的销售汇总指标",
                    "columns": [
                        {"name": "id", "type": "SERIAL", "nullable": False, "primary": True, "alias": "ID", "comment": ""},
                        {"name": "sale_date", "type": "DATE", "nullable": False, "alias": "日期", "comment": ""},
                        {"name": "category", "type": "VARCHAR(100)", "nullable": True, "alias": "分类", "comment": ""},
                        {"name": "brand", "type": "VARCHAR(100)", "nullable": True, "alias": "品牌", "comment": ""},
                        {"name": "city", "type": "VARCHAR(50)", "nullable": True, "alias": "城市", "comment": ""},
                        {"name": "order_count", "type": "INT", "nullable": False, "alias": "订单数", "comment": ""},
                        {"name": "gmv", "type": "DECIMAL(15,2)", "nullable": False, "alias": "GMV", "comment": ""},
                        {"name": "refund_amount", "type": "DECIMAL(15,2)", "nullable": False, "alias": "退款额", "comment": ""},
                        {"name": "new_user_count", "type": "INT", "nullable": False, "alias": "新用户数", "comment": "当日新增注册"},
                        {"name": "active_user_count", "type": "INT", "nullable": False, "alias": "活跃用户数", "comment": "当日有行为用户"},
                    ],
                },
                {
                    "name": "user_behavior_log", "alias": "用户行为日志", "description": "用户页面浏览、点击、加购等行为记录",
                    "columns": [
                        {"name": "id", "type": "SERIAL", "nullable": False, "primary": True, "alias": "ID", "comment": ""},
                        {"name": "user_id", "type": "BIGINT", "nullable": True, "alias": "用户ID", "comment": "NULL 表示未登录"},
                        {"name": "session_id", "type": "VARCHAR(64)", "nullable": False, "alias": "会话ID", "comment": ""},
                        {"name": "event_type", "type": "VARCHAR(30)", "nullable": False, "alias": "行为类型", "comment": "page_view/click/add_to_cart/purchase"},
                        {"name": "page_url", "type": "VARCHAR(500)", "nullable": True, "alias": "页面URL", "comment": ""},
                        {"name": "product_id", "type": "BIGINT", "nullable": True, "alias": "商品ID", "comment": "关联商品"},
                        {"name": "duration_ms", "type": "INT", "nullable": True, "alias": "停留时长(ms)", "comment": ""},
                        {"name": "occurred_at", "type": "TIMESTAMP", "nullable": False, "alias": "发生时间", "comment": ""},
                    ],
                },
                {
                    "name": "product_inventory_snapshot", "alias": "库存快照", "description": "每日商品库存快照（用于库存分析）",
                    "columns": [
                        {"name": "id", "type": "SERIAL", "nullable": False, "primary": True, "alias": "ID", "comment": ""},
                        {"name": "product_id", "type": "BIGINT", "nullable": False, "alias": "商品ID", "comment": ""},
                        {"name": "snapshot_date", "type": "DATE", "nullable": False, "alias": "快照日期", "comment": ""},
                        {"name": "stock_level", "type": "INT", "nullable": False, "alias": "库存量", "comment": "当日结束时库存"},
                        {"name": "sold_today", "type": "INT", "nullable": False, "alias": "当日销量", "comment": ""},
                        {"name": "restocked_today", "type": "INT", "nullable": False, "default": "0", "alias": "当日补货", "comment": ""},
                    ],
                },
                {
                    "name": "marketing_campaign", "alias": "营销活动表", "description": "平台营销活动及效果追踪",
                    "columns": [
                        {"name": "id", "type": "SERIAL", "nullable": False, "primary": True, "alias": "活动ID", "comment": ""},
                        {"name": "campaign_name", "type": "VARCHAR(200)", "nullable": False, "alias": "活动名称", "comment": ""},
                        {"name": "type", "type": "VARCHAR(30)", "nullable": False, "alias": "类型", "comment": "flash_sale/group_buy/coupon/discount"},
                        {"name": "start_date", "type": "DATE", "nullable": False, "alias": "开始日期", "comment": ""},
                        {"name": "end_date", "type": "DATE", "nullable": False, "alias": "结束日期", "comment": ""},
                        {"name": "budget", "type": "DECIMAL(12,2)", "nullable": True, "alias": "预算", "comment": ""},
                        {"name": "actual_cost", "type": "DECIMAL(12,2)", "nullable": True, "alias": "实际花费", "comment": ""},
                        {"name": "gmv_generated", "type": "DECIMAL(15,2)", "nullable": True, "alias": "带动GMV", "comment": ""},
                        {"name": "roi", "type": "DECIMAL(5,2)", "nullable": True, "alias": "ROI", "comment": "投入产出比"},
                        {"name": "participant_count", "type": "INT", "nullable": True, "alias": "参与人数", "comment": ""},
                    ],
                },
            ],
            "relationships": [],
            "metrics": [
                {"name": "日活跃用户", "expression": "COUNT(DISTINCT user_id)", "description": "当日有行为记录的用户数"},
                {"name": "转化率", "expression": "SUM(CASE WHEN event_type='purchase' THEN 1 ELSE 0 END) * 100.0 / NULLIF(SUM(CASE WHEN event_type='page_view' THEN 1 ELSE 0 END), 0)", "description": "浏览到购买的转化率"},
                {"name": "库存周转率", "expression": "SUM(sold_today) * 1.0 / NULLIF(AVG(stock_level), 0)", "description": "商品平均日销量 / 平均库存"},
                {"name": "活动ROI", "expression": "SUM(gmv_generated) / NULLIF(SUM(actual_cost), 0)", "description": "营销活动总GMV / 总花费"},
            ],
        }

        session.add_all([
            MetadataConfig(tenant_id=tenant_main.id, datasource_id=ds_orders.id, config=json.dumps(metadata_orders, ensure_ascii=False)),
            MetadataConfig(tenant_id=tenant_main.id, datasource_id=ds_analytics.id, config=json.dumps(metadata_analytics, ensure_ascii=False)),
        ])
        await session.commit()
        print(f"  Metadata: 生产库 8 表 + 7 关联 + 6 指标, 分析库 4 表 + 4 指标")

        # ════════════════════════════════════════
        # 5. Saved Queries (15 saved queries across scenarios)
        # ════════════════════════════════════════
        queries = [
            (analyst, ds_orders.id, "月度GMV趋势", "近12个月每月的GMV是多少",
             "SELECT DATE_FORMAT(created_at, '%%Y-%%m') AS month, COUNT(*) AS order_count, SUM(pay_amount) AS gmv, AVG(pay_amount) AS avg_order FROM orders WHERE status != 'cancelled' AND created_at >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH) GROUP BY month ORDER BY month"),
            (analyst, ds_orders.id, "VIP用户消费排行", "各VIP等级用户的消费总额和订单数",
             "SELECT u.vip_level, COUNT(DISTINCT o.id) AS order_count, SUM(o.pay_amount) AS total_spent, AVG(o.pay_amount) AS avg_order FROM users u LEFT JOIN orders o ON u.id = o.user_id AND o.status != 'cancelled' GROUP BY u.vip_level ORDER BY total_spent DESC"),
            (analyst, ds_orders.id, "商品分类销售TOP10", "各商品分类的销售额排名",
             "SELECT c.name AS category, COUNT(oi.id) AS item_count, SUM(oi.subtotal) AS sales FROM order_items oi JOIN products p ON oi.product_id = p.id JOIN categories c ON p.category_id = c.id GROUP BY c.name ORDER BY sales DESC LIMIT 10"),
            (analyst, ds_orders.id, "城市订单分布", "各城市的订单数量和金额分布",
             "SELECT shipping_city, COUNT(*) AS order_count, SUM(pay_amount) AS gmv, AVG(pay_amount) AS avg_amount FROM orders WHERE status != 'cancelled' GROUP BY shipping_city ORDER BY order_count DESC LIMIT 20"),
            (analyst, ds_orders.id, "近7日订单趋势", "最近7天每天的订单数量和金额",
             "SELECT DATE(created_at) AS date, COUNT(*) AS orders, SUM(pay_amount) AS revenue, SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) AS cancelled FROM orders WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) GROUP BY date ORDER BY date"),
            (analyst, ds_orders.id, "退款分析", "各订单状态的退款金额和退款率",
             "SELECT o.status, COUNT(r.id) AS refund_count, COALESCE(SUM(r.refund_amount), 0) AS refund_total, COUNT(DISTINCT o.id) AS order_count, COALESCE(SUM(r.refund_amount), 0) * 100.0 / NULLIF(SUM(o.pay_amount), 0) AS refund_rate FROM orders o LEFT JOIN refunds r ON o.id = r.order_id GROUP BY o.status ORDER BY refund_total DESC"),
            (analyst, ds_orders.id, "优惠券使用效果", "各优惠券的领取数、使用率和带动GMV",
             "SELECT c.name, c.type, c.value AS face_value, c.total_count, c.used_count, ROUND(c.used_count * 100.0 / NULLIF(c.total_count, 0), 1) AS usage_rate FROM coupons c WHERE c.is_active = 1 ORDER BY used_count DESC"),
            (analyst, ds_orders.id, "高价值用户", "累计消费TOP20的用户信息",
             "SELECT u.id, u.username, u.city, u.vip_level, u.total_spent, COUNT(DISTINCT o.id) AS order_count, MAX(o.created_at) AS last_order FROM users u JOIN orders o ON u.id = o.user_id WHERE o.status != 'cancelled' GROUP BY u.id ORDER BY u.total_spent DESC LIMIT 20"),
            (analyst, ds_orders.id, "库存预警", "库存低于100的在售商品",
             "SELECT p.name, c.name AS category, p.brand, p.price, p.stock, p.sales_count FROM products p JOIN categories c ON p.category_id = c.id WHERE p.is_on_sale = 1 AND p.stock < 100 ORDER BY p.stock ASC"),
            (ops, ds_orders.id, "今日订单概览", "今天有多少订单和GMV",
             "SELECT COUNT(*) AS today_orders, SUM(pay_amount) AS today_gmv, AVG(pay_amount) AS avg_amount, SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending, SUM(CASE WHEN status='paid' THEN 1 ELSE 0 END) AS paid FROM orders WHERE DATE(created_at) = CURDATE()"),
            (analyst, ds_analytics.id, "日活跃用户趋势", "近30天每天的活跃用户数",
             "SELECT sale_date, active_user_count AS dau, new_user_count AS new_users, gmv FROM daily_sales_summary WHERE sale_date >= CURRENT_DATE - INTERVAL '30 days' ORDER BY sale_date"),
            (analyst, ds_analytics.id, "用户行为漏斗", "浏览->加购->购买的转化率",
             "SELECT event_type, COUNT(DISTINCT session_id) AS sessions, COUNT(DISTINCT user_id) AS users FROM user_behavior_log WHERE occurred_at >= CURRENT_DATE - INTERVAL '7 days' GROUP BY event_type ORDER BY CASE event_type WHEN 'page_view' THEN 1 WHEN 'add_to_cart' THEN 2 WHEN 'purchase' THEN 3 END"),
            (analyst, ds_analytics.id, "营销活动ROI排行", "各营销活动的投入产出比",
             "SELECT campaign_name, type, budget, actual_cost, gmv_generated, roi, participant_count FROM marketing_campaign WHERE end_date <= CURRENT_DATE ORDER BY roi DESC NULLS LAST"),
            (viewer, ds_orders.id, "各支付方式占比", "不同支付方式的订单占比",
             "SELECT pay_type, COUNT(*) AS order_count, SUM(pay_amount) AS gmv, ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM orders WHERE status != 'cancelled'), 1) AS pct FROM orders WHERE status != 'cancelled' GROUP BY pay_type ORDER BY order_count DESC"),
            (viewer, ds_analytics.id, "商品库存周转", "各商品的库存周转率",
             "SELECT p.name, c.name AS category, AVG(s.stock_level) AS avg_stock, SUM(s.sold_today) AS total_sold, ROUND(SUM(s.sold_today) * 1.0 / NULLIF(AVG(s.stock_level), 0), 2) AS turnover_rate FROM product_inventory_snapshot s JOIN products p ON s.product_id = p.id JOIN categories c ON p.category_id = c.id GROUP BY p.id, p.name, c.name ORDER BY turnover_rate DESC LIMIT 20"),
        ]
        for actor, ds_id, name, text, sql in queries:
            sq = SavedQuery(
                tenant_id=tenant_main.id, user_id=actor.id,
                name=name, query_text=text, generated_sql=sql,
                datasource_id=ds_id,
            )
            session.add(sq)
        await session.commit()
        print(f"  Saved Queries: {len(queries)} 条")

        # ════════════════════════════════════════
        # 6. Feedback (12 feedback entries)
        # ════════════════════════════════════════
        feedbacks = [
            (analyst, "q-gmv-trend", "up", "趋势图很直观，一眼看出季节性波动"),
            (analyst, "q-vip-rank", "up", ""),
            (analyst, "q-category", "up", "分类维度的数据很有价值"),
            (viewer, "q-city", "down", "收货城市字段有很多NULL，数据质量不好"),
            (ops, "q-today", "up", "每天早上必看，运营日报的核心数据"),
            (analyst, "q-refund", "up", "退款率分析帮我们发现了几个问题品类"),
            (viewer, "q-payment", "down", "SQL 里 pay_type 的值没有做字典映射，显示的是英文代码"),
            (analyst, "q-funnel", "up", "漏斗分析帮助我们优化了商品详情页"),
            (ops, "q-stock", "up", "库存预警功能避免了多次断货"),
            (analyst, "q-campaign", "up", "ROI排行帮助我们优化了营销预算分配"),
            (viewer, "q-turnover", "down", "库存周转的SQL有语法错误，执行失败"),
            (admin, "q-high-value", "up", "高价值用户识别对运营策略很重要"),
        ]
        for actor, qid, rating, comment in feedbacks:
            fb = Feedback(tenant_id=tenant_main.id, user_id=actor.id, query_id=qid, rating=rating, comment=comment)
            session.add(fb)
        await session.commit()
        print(f"  Feedback: {len(feedbacks)} 条 (8 up, 4 down)")

        # ════════════════════════════════════════
        # 7. Audit Logs (30 entries covering all action types)
        # ════════════════════════════════════════
        audit_actions = [
            (admin, "USER_LOGIN", "user", str(admin.id), "ip=192.168.1.10"),
            (analyst, "USER_LOGIN", "user", str(analyst.id), "ip=192.168.1.20"),
            (viewer, "USER_LOGIN", "user", str(viewer.id), "ip=192.168.1.30"),
            (ops, "USER_LOGIN", "user", str(ops.id), "ip=192.168.1.40"),
            (admin, "DATASOURCE_CREATE", "datasource", str(ds_orders.id), "name=生产订单库 type=mysql"),
            (admin, "DATASOURCE_CREATE", "datasource", str(ds_analytics.id), "name=数据分析仓库 type=postgresql"),
            (admin, "DATASOURCE_TEST", "datasource", str(ds_orders.id), "result=success latency=45ms"),
            (admin, "DATASOURCE_TEST", "datasource", str(ds_analytics.id), "result=success latency=120ms"),
            (admin, "DATASOURCE_SCAN", "datasource", str(ds_orders.id), "tables=8 models_scanned=8"),
            (admin, "DATASOURCE_SCAN", "datasource", str(ds_analytics.id), "tables=4 models_scanned=4"),
            (analyst, "QUERY_EXECUTE", "query", "q-gmv-trend", "question=月度GMV趋势 intent=DataQuery success=true rows=12"),
            (analyst, "QUERY_EXECUTE", "query", "q-vip-rank", "question=VIP用户排行 intent=DataQuery success=true rows=6"),
            (analyst, "QUERY_EXECUTE", "query", "q-category", "question=分类销售TOP10 intent=DataQuery success=true rows=10"),
            (analyst, "QUERY_EXECUTE", "query", "q-refund", "question=退款分析 intent=DataQuery success=true rows=5"),
            (ops, "QUERY_EXECUTE", "query", "q-today", "question=今日订单概览 intent=DataQuery success=true rows=1"),
            (ops, "QUERY_EXECUTE", "query", "q-stock", "question=库存预警 intent=DataQuery success=true rows=15"),
            (analyst, "QUERY_SAVE", "saved_query", "q-gmv-trend", "name=月度GMV趋势"),
            (analyst, "QUERY_SAVE", "saved_query", "q-vip-rank", "name=VIP用户消费排行"),
            (viewer, "QUERY_EXECUTE", "query", "", "question=哪个分类卖得最好 intent=DataQuery success=true rows=10"),
            (viewer, "QUERY_EXECUTE", "query", "", "question=你好 intent=Other success=false error=非数据查询意图"),
            (viewer, "QUERY_EXECUTE", "query", "", "question=帮我写首诗 intent=Other success=false error=非数据查询意图"),
            (analyst, "QUERY_EXPORT", "query", "q-gmv-trend", "format=csv rows=12"),
            (analyst, "QUERY_EXPORT", "query", "q-category", "format=csv rows=10"),
            (analyst, "QUERY_EXECUTE", "query", "", "question=各城市用户数量 intent=DataQuery success=false error=SQL执行超时"),
            (admin, "USER_CREATE", "user", str(ops.id), "email=ops@chatbi.com role=user"),
            (admin, "DATASOURCE_UPDATE", "datasource", str(ds_orders.id), "fields=name,host"),
            (viewer, "FEEDBACK_SUBMIT", "feedback", "q-city", "rating=down"),
            (analyst, "FEEDBACK_SUBMIT", "feedback", "q-gmv-trend", "rating=up"),
            (analyst, "QUERY_EXECUTE", "query", "q-funnel", "question=用户行为漏斗 intent=DataQuery success=true rows=3"),
            (ops, "DATA_MODEL_UPDATE", "metadata", str(ds_orders.id), "tables=8 relationships=7 metrics=6"),
        ]
        for actor, action, rtype, rid, details in audit_actions:
            await log_action(session, str(tenant_main.id), str(actor.id), action, rtype, rid, details)
        await session.commit()
        print(f"  Audit Logs: {len(audit_actions)} 条")

        # ════════════════════════════════════════
        # 8. Analytics Events (40 events)
        # ════════════════════════════════════════
        event_names = [
            "user_login", "user_login", "user_login", "user_login", "user_login",
            "datasource_create", "datasource_create",
            "datasource_test", "datasource_test",
            "datasource_scan", "datasource_scan",
            "query_execute", "query_execute", "query_execute", "query_execute",
            "query_execute", "query_execute", "query_execute", "query_execute",
            "query_success", "query_success", "query_success", "query_success",
            "query_success", "query_success",
            "query_error", "query_error",
            "query_save", "query_save", "query_save",
            "query_export_csv", "query_export_csv",
            "chart_view", "chart_view", "chart_view", "chart_view",
            "chart_type_change", "chart_type_change",
            "feedback_submit", "feedback_submit",
        ]
        event_data_samples = [
            {}, {"method": "email"}, {}, {}, {},
            {"name": "生产订单库", "type": "mysql"}, {"name": "数据分析仓库", "type": "postgresql"},
            {"success": True, "latency_ms": 45}, {"success": True, "latency_ms": 120},
            {"tables_scanned": 8, "new_tables": 8}, {"tables_scanned": 4, "new_tables": 0},
            {"question": "月度GMV趋势"}, {"question": "VIP用户消费排行"},
            {"question": "商品分类销售TOP10"}, {"question": "城市订单分布"},
            {"question": "退款分析"}, {"question": "今日订单概览"},
            {"question": "你好"}, {"question": "帮我写首诗"},
            {"rows": 12, "latency_ms": 1200}, {"rows": 6, "latency_ms": 800},
            {"rows": 10, "latency_ms": 1500}, {"rows": 5, "latency_ms": 2000},
            {"rows": 1, "latency_ms": 300}, {"rows": 15, "latency_ms": 600},
            {"error": "SQL执行超时", "latency_ms": 30000}, {"error": "非数据查询意图"},
            {"name": "月度GMV趋势"}, {"name": "VIP用户消费排行"}, {"name": "城市订单分布"},
            {"format": "csv", "rows": 12}, {"format": "csv", "rows": 10},
            {"chart_type": "line"}, {"chart_type": "bar"},
            {"chart_type": "table"}, {"chart_type": "pie"},
            {"from": "table", "to": "bar"}, {"from": "bar", "to": "line"},
            {"rating": "up"}, {"rating": "down"},
        ]
        actors_list = [admin, analyst, viewer, ops]
        for i, (ename, edata) in enumerate(zip(event_names, event_data_samples)):
            actor = actors_list[i % len(actors_list)]
            await track_event(session, str(tenant_main.id), str(actor.id), ename, edata)
        await session.commit()
        print(f"  Analytics Events: {len(event_names)} 条")

        # ════════════════════════════════════════
        # 9. Conversations (5 conversations with multi-turn messages)
        # ════════════════════════════════════════
        conversations = [
            (analyst, ds_orders.id, "销售分析", [
                {"role": "user", "content": "近12个月每月的GMV是多少", "created_at": _past(hours=5).isoformat()},
                {"role": "assistant", "content": "已为您查询到近12个月的GMV数据", "sql": "SELECT DATE_FORMAT(created_at, '%Y-%m') AS month, SUM(pay_amount) AS gmv FROM orders WHERE status != 'cancelled' AND created_at >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH) GROUP BY month", "rows": 12, "columns": [{"name": "month"}, {"name": "gmv"}], "chart_type": "line", "created_at": _past(hours=5, minutes=59).isoformat()},
                {"role": "user", "content": "按月维度再拆分支付方式占比", "created_at": _past(hours=4).isoformat()},
                {"role": "assistant", "content": "按月拆分的支付方式占比如下", "sql": "SELECT DATE_FORMAT(created_at, '%Y-%m') AS month, pay_type, COUNT(*) AS cnt, SUM(pay_amount) AS gmv FROM orders WHERE status != 'cancelled' GROUP BY month, pay_type ORDER BY month", "rows": 48, "columns": [{"name": "month"}, {"name": "pay_type"}, {"name": "cnt"}, {"name": "gmv"}], "chart_type": "stacked_bar", "created_at": _past(hours=3, minutes=59).isoformat()},
            ]),
            (analyst, ds_orders.id, "用户画像", [
                {"role": "user", "content": "各VIP等级的用户数量", "created_at": _past(days=1).isoformat()},
                {"role": "assistant", "content": "各VIP等级用户数量查询结果", "sql": "SELECT vip_level, COUNT(*) AS user_count FROM users GROUP BY vip_level ORDER BY vip_level", "rows": 6, "columns": [{"name": "vip_level"}, {"name": "user_count"}], "chart_type": "bar", "created_at": _past(days=1, hours=23, minutes=59).isoformat()},
            ]),
            (ops, ds_orders.id, "库存管理", [
                {"role": "user", "content": "库存低于100的商品有哪些", "created_at": _past(hours=2).isoformat()},
                {"role": "assistant", "content": "以下是库存低于100的商品列表", "sql": "SELECT p.name, p.stock, p.sales_count FROM products p WHERE p.is_on_sale = 1 AND p.stock < 100 ORDER BY p.stock ASC", "rows": 23, "columns": [{"name": "name"}, {"name": "stock"}, {"name": "sales_count"}], "chart_type": "table", "created_at": _past(hours=1, minutes=59).isoformat()},
                {"role": "user", "content": "按销量从高到低排序", "created_at": _past(hours=1).isoformat()},
                {"role": "assistant", "content": "已按销量排序", "sql": "SELECT p.name, p.stock, p.sales_count FROM products p WHERE p.is_on_sale = 1 AND p.stock < 100 ORDER BY p.sales_count DESC", "rows": 23, "columns": [{"name": "name"}, {"name": "stock"}, {"name": "sales_count"}], "chart_type": "table", "created_at": _past(minutes=59).isoformat()},
            ]),
            (viewer, ds_analytics.id, "营销活动", [
                {"role": "user", "content": "各营销活动的ROI排行", "created_at": _past(days=3).isoformat()},
                {"role": "assistant", "content": "营销活动ROI排行如下", "sql": "SELECT campaign_name, type, budget, actual_cost, gmv_generated, roi FROM marketing_campaign ORDER BY roi DESC NULLS LAST", "rows": 15, "columns": [{"name": "campaign_name"}, {"name": "type"}, {"name": "budget"}, {"name": "actual_cost"}, {"name": "gmv_generated"}, {"name": "roi"}], "chart_type": "bar", "created_at": _past(days=2, hours=23, minutes=59).isoformat()},
            ]),
            (viewer, ds_orders.id, "", [
                {"role": "user", "content": "你好", "created_at": _past(minutes=10).isoformat()},
                {"role": "assistant", "content": "您好！我是 ChatBI 助手，请告诉我您想查询的数据。", "created_at": _past(minutes=9).isoformat()},
            ]),
        ]
        for actor, ds_id, title, messages in conversations:
            conv = Conversation(
                tenant_id=tenant_main.id, user_id=actor.id,
                title=title, datasource_id=str(ds_id),
                messages=json.dumps(messages, ensure_ascii=False),
            )
            session.add(conv)
        await session.commit()
        print(f"  Conversations: {len(conversations)} 个对话 (共 {sum(len(m) for _,_,_,m in conversations)} 条消息)")

        await session.close()

        # ════════════════════════════════════════
        # Summary
        # ════════════════════════════════════════
        print("\n" + "=" * 70)
        print("  复杂测试数据生成完成！（通过 Service/ORM 层）")
        print("=" * 70)
        print(f"\n  租户: 2 （杭州星辰科技有限公司 + Demo 演示租户）")
        print(f"\n  用户 (6):")
        print(f"    - admin@chatbi.com     管理员  密码: Test1234!  [正常]")
        print(f"    - analyst@chatbi.com   用户    密码: Test1234!  [正常]")
        print(f"    - viewer@chatbi.com    用户    密码: Test1234!  [正常]")
        print(f"    - ops@chatbi.com       用户    密码: Test1234!  [正常]")
        print(f"    - locked@chatbi.com    用户    密码: Test1234!  [已锁定+未验证]")
        print(f"    - demo@chatbi.com      管理员  密码: Test1234!  [Demo租户]")
        print(f"\n  数据源 (4):")
        print(f"    - 生产订单库 (MySQL)      [活跃]  8张表 + 7关联 + 6指标")
        print(f"    - 数据分析仓库 (PostgreSQL) [活跃]  4张表 + 4指标")
        print(f"    - Demo 数据源 (MySQL)     [异常]  连接超时")
        print(f"    - 测试库 (MySQL)          [异常]  Access denied")
        print(f"\n  元数据模型:")
        print(f"    生产库: users, products, categories, orders, order_items,")
        print(f"          coupons, user_coupons, refunds  (8表)")
        print(f"    分析库: daily_sales_summary, user_behavior_log,")
        print(f"          product_inventory_snapshot, marketing_campaign  (4表)")
        print(f"\n  保存的查询: 15 条（覆盖趋势/排行/分布/漏斗/ROI/库存等场景）")
        print(f"  用户反馈:   12 条（8 好评 + 4 差评）")
        print(f"  审计日志:   30 条（登录/创建/查询/导出/错误等全类型）")
        print(f"  埋点事件:   40 条（完整用户行为链路）")
        print(f"  对话记录:   5 个（共 13 条消息，含多轮对话）")
        print("=" * 70)


if __name__ == "__main__":
    asyncio.run(seed())
