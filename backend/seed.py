"""Seed script: populate MySQL with tenants, users, datasources, and business data."""
import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from app.core.security import hash_password
from app.core.encryption import encrypt_value
from app.db.session import async_session_factory
from app.db.models import (
    Tenant, User, DataSource, MetadataConfig,
    AuditLog, SavedQuery, Feedback, AnalyticsEvent, Conversation,
)

# Database connection info from environment variables
SEED_DB_HOST = os.environ.get('SEED_DB_HOST', '192.168.3.110')
SEED_DB_PORT = int(os.environ.get('SEED_DB_PORT', '3306'))
SEED_DB_USER = os.environ.get('SEED_DB_USER', 'root')
SEED_DB_PASS = os.environ.get('SEED_DB_PASS', 'yjt_mysql')
# Single test database: chatbi_test
TEST_DB_NAME = os.environ.get('TEST_DB_NAME', 'chatbi_test')

TENANT_1 = uuid.UUID('11111111-1111-1111-1111-111111111111')
TENANT_2 = uuid.UUID('22222222-2222-2222-2222-222222222222')

DS_1 = uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
DS_2 = uuid.UUID('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')
DS_3 = uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')
DS_4 = uuid.UUID('dddddddd-dddd-dddd-dddd-dddddddddddd')

USER_ADMIN   = uuid.UUID('aaaaaaaa-0000-0000-0000-000000000001')
USER_ANALYST = uuid.UUID('aaaaaaaa-0000-0000-0000-000000000002')
USER_VIEWER  = uuid.UUID('aaaaaaaa-0000-0000-0000-000000000003')
USER_OPS     = uuid.UUID('aaaaaaaa-0000-0000-0000-000000000004')
USER_LOCKED  = uuid.UUID('aaaaaaaa-0000-0000-0000-000000000005')
USER_DEMO    = uuid.UUID('bbbbbbbb-0000-0000-0000-000000000001')

NOW = datetime.now(timezone.utc)


async def seed():
    async with async_session_factory() as session:
        # Truncate all tables in dependency order (children first, then parents)
        for table_name in [
            'analytics_events', 'feedback', 'saved_queries', 'conversations',
            'audit_logs', 'metadata_config_versions', 'metadata_configs',
            'data_sources', 'users', 'tenants',
        ]:
            await session.execute(text(f'TRUNCATE TABLE {table_name}'))
        await session.commit()

        tenants = [
            Tenant(id=TENANT_1, name='杭州星辰科技有限公司'),
            Tenant(id=TENANT_2, name='Demo 演示租户'),
        ]
        for t in tenants:
            session.add(t)
        await session.commit()

        pw_hash = hash_password('Test1234!')
        users = [
            User(id=USER_ADMIN, tenant_id=TENANT_1, email='admin@chatbi.com',
                 password_hash=pw_hash, is_active=True, email_verified=True, role='admin'),
            User(id=USER_ANALYST, tenant_id=TENANT_1, email='analyst@chatbi.com',
                 password_hash=pw_hash, is_active=True, email_verified=True, role='user'),
            User(id=USER_VIEWER, tenant_id=TENANT_1, email='viewer@chatbi.com',
                 password_hash=pw_hash, is_active=True, email_verified=True, role='user'),
            User(id=USER_OPS, tenant_id=TENANT_1, email='ops@chatbi.com',
                 password_hash=pw_hash, is_active=True, email_verified=True, role='user'),
            User(id=USER_LOCKED, tenant_id=TENANT_1, email='locked@chatbi.com',
                 password_hash=pw_hash, is_active=False, email_verified=False,
                 is_locked=True, lock_until=NOW + timedelta(hours=1),
                 failed_login_attempts=5, role='user'),
            User(id=USER_DEMO, tenant_id=TENANT_2, email='demo@chatbi.com',
                 password_hash=pw_hash, is_active=True, email_verified=True, role='admin'),
        ]
        for u in users:
            session.add(u)
        await session.commit()

        enc_user = encrypt_value(SEED_DB_USER)
        enc_pass = encrypt_value(SEED_DB_PASS)
        datasources = [
            DataSource(id=DS_1, tenant_id=TENANT_1, name='电商业务库',
                       db_type='mysql', host=SEED_DB_HOST, port=SEED_DB_PORT,
                       database_name=TEST_DB_NAME, username_encrypted=enc_user,
                       password_encrypted=enc_pass, is_active=True, last_health_check=NOW),
            DataSource(id=DS_2, tenant_id=TENANT_1, name='ChatBI 自身库',
                       db_type='mysql', host=SEED_DB_HOST, port=SEED_DB_PORT,
                       database_name='chatbi', username_encrypted=enc_user,
                       password_encrypted=enc_pass, is_active=True, last_health_check=NOW),
            DataSource(id=DS_3, tenant_id=TENANT_1, name='ChatBI 元数据',
                       db_type='mysql', host=SEED_DB_HOST, port=SEED_DB_PORT,
                       database_name='chatbi', username_encrypted=enc_user,
                       password_encrypted=enc_pass, is_active=True, last_health_check=NOW),
            DataSource(id=DS_4, tenant_id=TENANT_2, name='Demo 数据源',
                       db_type='mysql', host=SEED_DB_HOST, port=SEED_DB_PORT,
                       database_name=TEST_DB_NAME, username_encrypted=enc_user,
                       password_encrypted=enc_pass, is_active=True, last_health_check=NOW),
        ]
        for ds in datasources:
            session.add(ds)
        await session.commit()

        ecommerce_schema = {
            'models': [
                {'name': 't_users', 'comment': '用户表', 'columns': [
                    {'name': 'id', 'type': 'bigint', 'nullable': False, 'comment': '用户ID', 'is_pk': True},
                    {'name': 'username', 'type': 'varchar(100)', 'nullable': False, 'comment': '用户名'},
                    {'name': 'email', 'type': 'varchar(255)', 'nullable': True, 'comment': '邮箱'},
                    {'name': 'phone', 'type': 'varchar(20)', 'nullable': True, 'comment': '手机号'},
                    {'name': 'city', 'type': 'varchar(100)', 'nullable': True, 'comment': '城市'},
                    {'name': 'province', 'type': 'varchar(50)', 'nullable': True, 'comment': '省份'},
                    {'name': 'vip_level', 'type': 'int', 'nullable': False, 'comment': 'VIP等级'},
                    {'name': 'points', 'type': 'int', 'nullable': False, 'comment': '积分'},
                    {'name': 'total_orders', 'type': 'int', 'nullable': False, 'comment': '总订单数'},
                    {'name': 'total_spent', 'type': 'decimal(12,2)', 'nullable': False, 'comment': '总消费额'},
                    {'name': 'created_at', 'type': 'datetime', 'nullable': False, 'comment': '注册时间'},
                ]},
                {'name': 't_orders', 'comment': '订单表', 'columns': [
                    {'name': 'id', 'type': 'bigint', 'nullable': False, 'comment': '订单ID', 'is_pk': True},
                    {'name': 'user_id', 'type': 'bigint', 'nullable': False, 'comment': '用户ID'},
                    {'name': 'order_no', 'type': 'varchar(50)', 'nullable': False, 'comment': '订单号'},
                    {'name': 'total_amount', 'type': 'decimal(12,2)', 'nullable': False, 'comment': '订单金额'},
                    {'name': 'discount_amount', 'type': 'decimal(10,2)', 'nullable': True, 'comment': '优惠金额'},
                    {'name': 'shipping_fee', 'type': 'decimal(10,2)', 'nullable': True, 'comment': '运费'},
                    {'name': 'coupon_code', 'type': 'varchar(50)', 'nullable': True, 'comment': '优惠券码'},
                    {'name': 'status', 'type': 'varchar(20)', 'nullable': False, 'comment': '订单状态'},
                    {'name': 'city', 'type': 'varchar(100)', 'nullable': True, 'comment': '城市'},
                    {'name': 'province', 'type': 'varchar(50)', 'nullable': True, 'comment': '省份'},
                    {'name': 'note', 'type': 'varchar(500)', 'nullable': True, 'comment': '备注'},
                    {'name': 'created_at', 'type': 'datetime', 'nullable': False, 'comment': '创建时间'},
                    {'name': 'paid_at', 'type': 'datetime', 'nullable': True, 'comment': '支付时间'},
                    {'name': 'shipped_at', 'type': 'datetime', 'nullable': True, 'comment': '发货时间'},
                    {'name': 'delivered_at', 'type': 'datetime', 'nullable': True, 'comment': '签收时间'},
                    {'name': 'completed_at', 'type': 'datetime', 'nullable': True, 'comment': '完成时间'},
                ]},
                {'name': 't_order_items', 'comment': '订单明细表', 'columns': [
                    {'name': 'id', 'type': 'bigint', 'nullable': False, 'comment': '明细ID', 'is_pk': True},
                    {'name': 'order_id', 'type': 'bigint', 'nullable': False, 'comment': '订单ID'},
                    {'name': 'product_id', 'type': 'bigint', 'nullable': False, 'comment': '商品ID'},
                    {'name': 'product_name', 'type': 'varchar(200)', 'nullable': True, 'comment': '商品名'},
                    {'name': 'quantity', 'type': 'int', 'nullable': False, 'comment': '数量'},
                    {'name': 'price', 'type': 'decimal(10,2)', 'nullable': False, 'comment': '单价'},
                    {'name': 'subtotal', 'type': 'decimal(10,2)', 'nullable': False, 'comment': '小计'},
                ]},
                {'name': 't_products', 'comment': '商品表', 'columns': [
                    {'name': 'id', 'type': 'bigint', 'nullable': False, 'comment': '商品ID', 'is_pk': True},
                    {'name': 'name', 'type': 'varchar(200)', 'nullable': False, 'comment': '商品名'},
                    {'name': 'category_id', 'type': 'bigint', 'nullable': False, 'comment': '分类ID'},
                    {'name': 'brand_id', 'type': 'bigint', 'nullable': True, 'comment': '品牌ID'},
                    {'name': 'price', 'type': 'decimal(10,2)', 'nullable': False, 'comment': '售价'},
                    {'name': 'cost_price', 'type': 'decimal(10,2)', 'nullable': False, 'comment': '成本价'},
                    {'name': 'market_price', 'type': 'decimal(10,2)', 'nullable': True, 'comment': '市场价'},
                    {'name': 'stock', 'type': 'int', 'nullable': False, 'comment': '库存'},
                    {'name': 'sku', 'type': 'varchar(50)', 'nullable': True, 'comment': 'SKU'},
                    {'name': 'status', 'type': 'varchar(20)', 'nullable': False, 'comment': '状态'},
                    {'name': 'tags', 'type': 'varchar(500)', 'nullable': True, 'comment': '标签'},
                ]},
                {'name': 't_categories', 'comment': '分类表', 'columns': [
                    {'name': 'id', 'type': 'bigint', 'nullable': False, 'comment': '分类ID', 'is_pk': True},
                    {'name': 'name', 'type': 'varchar(100)', 'nullable': False, 'comment': '分类名'},
                    {'name': 'parent_id', 'type': 'bigint', 'nullable': True, 'comment': '父分类ID'},
                    {'name': 'sort_order', 'type': 'int', 'nullable': False, 'comment': '排序'},
                    {'name': 'is_active', 'type': 'tinyint', 'nullable': False, 'comment': '是否启用'},
                ]},
                {'name': 't_brands', 'comment': '品牌表', 'columns': [
                    {'name': 'id', 'type': 'bigint', 'nullable': False, 'comment': '品牌ID', 'is_pk': True},
                    {'name': 'name', 'type': 'varchar(100)', 'nullable': False, 'comment': '品牌名'},
                    {'name': 'description', 'type': 'text', 'nullable': True, 'comment': '描述'},
                    {'name': 'is_active', 'type': 'tinyint', 'nullable': False, 'comment': '是否启用'},
                ]},
                {'name': 't_payments', 'comment': '支付记录表', 'columns': [
                    {'name': 'id', 'type': 'bigint', 'nullable': False, 'comment': '支付ID', 'is_pk': True},
                    {'name': 'order_id', 'type': 'bigint', 'nullable': False, 'comment': '订单ID'},
                    {'name': 'amount', 'type': 'decimal(10,2)', 'nullable': False, 'comment': '支付金额'},
                    {'name': 'method', 'type': 'varchar(50)', 'nullable': False, 'comment': '支付方式'},
                    {'name': 'transaction_id', 'type': 'varchar(100)', 'nullable': True, 'comment': '交易号'},
                    {'name': 'status', 'type': 'varchar(20)', 'nullable': False, 'comment': '状态'},
                    {'name': 'refund_amount', 'type': 'decimal(10,2)', 'nullable': True, 'comment': '退款金额'},
                    {'name': 'paid_at', 'type': 'datetime', 'nullable': False, 'comment': '支付时间'},
                ]},
                {'name': 't_shipping', 'comment': '物流表', 'columns': [
                    {'name': 'id', 'type': 'bigint', 'nullable': False, 'comment': '物流ID', 'is_pk': True},
                    {'name': 'order_id', 'type': 'bigint', 'nullable': False, 'comment': '订单ID'},
                    {'name': 'carrier', 'type': 'varchar(100)', 'nullable': True, 'comment': '快递公司'},
                    {'name': 'tracking_no', 'type': 'varchar(100)', 'nullable': True, 'comment': '运单号'},
                    {'name': 'shipped_at', 'type': 'datetime', 'nullable': False, 'comment': '发货时间'},
                    {'name': 'delivered_at', 'type': 'datetime', 'nullable': True, 'comment': '签收时间'},
                    {'name': 'status', 'type': 'varchar(20)', 'nullable': False, 'comment': '物流状态'},
                ]},
                {'name': 't_reviews', 'comment': '评价表', 'columns': [
                    {'name': 'id', 'type': 'bigint', 'nullable': False, 'comment': '评价ID', 'is_pk': True},
                    {'name': 'order_id', 'type': 'bigint', 'nullable': False, 'comment': '订单ID'},
                    {'name': 'product_id', 'type': 'bigint', 'nullable': False, 'comment': '商品ID'},
                    {'name': 'user_id', 'type': 'bigint', 'nullable': False, 'comment': '用户ID'},
                    {'name': 'rating', 'type': 'int', 'nullable': False, 'comment': '评分(1-5)'},
                    {'name': 'content', 'type': 'varchar(1000)', 'nullable': True, 'comment': '评价内容'},
                    {'name': 'images', 'type': 'int', 'nullable': True, 'comment': '图片数'},
                    {'name': 'helpful_count', 'type': 'int', 'nullable': True, 'comment': '有帮助数'},
                ]},
                {'name': 't_returns', 'comment': '退换货表', 'columns': [
                    {'name': 'id', 'type': 'bigint', 'nullable': False, 'comment': '退货ID', 'is_pk': True},
                    {'name': 'order_id', 'type': 'bigint', 'nullable': False, 'comment': '订单ID'},
                    {'name': 'user_id', 'type': 'bigint', 'nullable': False, 'comment': '用户ID'},
                    {'name': 'reason', 'type': 'varchar(200)', 'nullable': True, 'comment': '退货原因'},
                    {'name': 'refund_amount', 'type': 'decimal(10,2)', 'nullable': False, 'comment': '退款金额'},
                    {'name': 'status', 'type': 'varchar(20)', 'nullable': False, 'comment': '状态'},
                    {'name': 'created_at', 'type': 'datetime', 'nullable': False, 'comment': '申请时间'},
                ]},
                {'name': 't_addresses', 'comment': '收货地址表', 'columns': [
                    {'name': 'id', 'type': 'bigint', 'nullable': False, 'comment': '地址ID', 'is_pk': True},
                    {'name': 'user_id', 'type': 'bigint', 'nullable': False, 'comment': '用户ID'},
                    {'name': 'name', 'type': 'varchar(100)', 'nullable': False, 'comment': '收货人'},
                    {'name': 'phone', 'type': 'varchar(20)', 'nullable': False, 'comment': '电话'},
                    {'name': 'province', 'type': 'varchar(50)', 'nullable': False, 'comment': '省份'},
                    {'name': 'city', 'type': 'varchar(100)', 'nullable': False, 'comment': '城市'},
                    {'name': 'district', 'type': 'varchar(100)', 'nullable': True, 'comment': '区县'},
                    {'name': 'detail', 'type': 'varchar(500)', 'nullable': False, 'comment': '详细地址'},
                ]},
                {'name': 't_coupons', 'comment': '优惠券表', 'columns': [
                    {'name': 'id', 'type': 'bigint', 'nullable': False, 'comment': '优惠券ID', 'is_pk': True},
                    {'name': 'code', 'type': 'varchar(50)', 'nullable': False, 'comment': '优惠券码'},
                    {'name': 'name', 'type': 'varchar(200)', 'nullable': True, 'comment': '名称'},
                    {'name': 'discount_type', 'type': 'varchar(20)', 'nullable': False, 'comment': '类型'},
                    {'name': 'discount_value', 'type': 'decimal(10,2)', 'nullable': False, 'comment': '优惠值'},
                    {'name': 'min_order', 'type': 'decimal(10,2)', 'nullable': True, 'comment': '最低订单额'},
                    {'name': 'max_uses', 'type': 'int', 'nullable': True, 'comment': '最大使用次数'},
                    {'name': 'valid_from', 'type': 'datetime', 'nullable': False, 'comment': '有效开始'},
                    {'name': 'valid_until', 'type': 'datetime', 'nullable': False, 'comment': '有效结束'},
                ]},
                {'name': 't_user_coupons', 'comment': '用户优惠券表', 'columns': [
                    {'name': 'id', 'type': 'bigint', 'nullable': False, 'comment': 'ID', 'is_pk': True},
                    {'name': 'user_id', 'type': 'bigint', 'nullable': False, 'comment': '用户ID'},
                    {'name': 'coupon_id', 'type': 'bigint', 'nullable': False, 'comment': '优惠券ID'},
                    {'name': 'status', 'type': 'varchar(20)', 'nullable': False, 'comment': '状态'},
                ]},
                {'name': 't_warehouses', 'comment': '仓库表', 'columns': [
                    {'name': 'id', 'type': 'bigint', 'nullable': False, 'comment': '仓库ID', 'is_pk': True},
                    {'name': 'name', 'type': 'varchar(200)', 'nullable': False, 'comment': '仓库名'},
                    {'name': 'city', 'type': 'varchar(100)', 'nullable': True, 'comment': '城市'},
                    {'name': 'capacity', 'type': 'int', 'nullable': True, 'comment': '容量'},
                    {'name': 'address', 'type': 'varchar(500)', 'nullable': True, 'comment': '地址'},
                ]},
                {'name': 't_inventory', 'comment': '库存表', 'columns': [
                    {'name': 'id', 'type': 'bigint', 'nullable': False, 'comment': '库存ID', 'is_pk': True},
                    {'name': 'product_id', 'type': 'bigint', 'nullable': False, 'comment': '商品ID'},
                    {'name': 'warehouse_id', 'type': 'bigint', 'nullable': False, 'comment': '仓库ID'},
                    {'name': 'quantity', 'type': 'int', 'nullable': False, 'comment': '数量'},
                    {'name': 'reserved', 'type': 'int', 'nullable': False, 'comment': '预留数量'},
                    {'name': 'min_stock', 'type': 'int', 'nullable': True, 'comment': '最低库存预警'},
                ]},
            ],
            'relationships': [
                {'from_table': 't_orders', 'from_column': 'user_id', 'to_table': 't_users', 'to_column': 'id'},
                {'from_table': 't_order_items', 'from_column': 'order_id', 'to_table': 't_orders', 'to_column': 'id'},
                {'from_table': 't_order_items', 'from_column': 'product_id', 'to_table': 't_products', 'to_column': 'id'},
                {'from_table': 't_payments', 'from_column': 'order_id', 'to_table': 't_orders', 'to_column': 'id'},
                {'from_table': 't_shipping', 'from_column': 'order_id', 'to_table': 't_orders', 'to_column': 'id'},
                {'from_table': 't_reviews', 'from_column': 'order_id', 'to_table': 't_orders', 'to_column': 'id'},
                {'from_table': 't_reviews', 'from_column': 'product_id', 'to_table': 't_products', 'to_column': 'id'},
                {'from_table': 't_returns', 'from_column': 'order_id', 'to_table': 't_orders', 'to_column': 'id'},
                {'from_table': 't_returns', 'from_column': 'user_id', 'to_table': 't_users', 'to_column': 'id'},
                {'from_table': 't_addresses', 'from_column': 'user_id', 'to_table': 't_users', 'to_column': 'id'},
                {'from_table': 't_user_coupons', 'from_column': 'user_id', 'to_table': 't_users', 'to_column': 'id'},
                {'from_table': 't_user_coupons', 'from_column': 'coupon_id', 'to_table': 't_coupons', 'to_column': 'id'},
                {'from_table': 't_inventory', 'from_column': 'product_id', 'to_table': 't_products', 'to_column': 'id'},
                {'from_table': 't_inventory', 'from_column': 'warehouse_id', 'to_table': 't_warehouses', 'to_column': 'id'},
                {'from_table': 't_products', 'from_column': 'category_id', 'to_table': 't_categories', 'to_column': 'id'},
                {'from_table': 't_products', 'from_column': 'brand_id', 'to_table': 't_brands', 'to_column': 'id'},
            ],
            'metrics': [
                {'name': 'GMV', 'expression': 'SUM(t_orders.total_amount)', 'description': '总交易额'},
                {'name': '订单数', 'expression': 'COUNT(t_orders.id)', 'description': '总订单数'},
                {'name': '客单价', 'expression': 'AVG(t_orders.total_amount)', 'description': '平均订单金额'},
                {'name': '退款率', 'expression': 'COUNT(CASE WHEN t_payments.status="refunded" THEN 1 END) / COUNT(t_payments.id) * 100', 'description': '退款订单占比'},
                {'name': '平均评分', 'expression': 'AVG(t_reviews.rating)', 'description': '平均评价分数'},
            ],
        }
        metadata_configs = [
            MetadataConfig(id=uuid.UUID('eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee'),
                          tenant_id=TENANT_1, datasource_id=DS_1,
                          config=json.dumps(ecommerce_schema, ensure_ascii=False)),
            MetadataConfig(id=uuid.UUID('ffffffff-ffff-ffff-ffff-ffffffffffff'),
                          tenant_id=TENANT_1, datasource_id=DS_2,
                          config=json.dumps({'models': [], 'relationships': [], 'metrics': []})),
        ]
        for mc in metadata_configs:
            session.add(mc)
        await session.commit()

        audit_actions = [('USER_LOGIN', ''), ('DATASOURCE_CREATE', str(DS_1)),
                         ('QUERY_EXECUTE', ''), ('DATASOURCE_SCAN', str(DS_1))]
        for i in range(30):
            action, rid = audit_actions[i % len(audit_actions)]
            user_id = [USER_ADMIN, USER_ANALYST, USER_VIEWER, USER_OPS][i % 4]
            session.add(AuditLog(
                id=uuid.uuid4(), tenant_id=TENANT_1, user_id=user_id,
                action=action, resource_type='', resource_id=rid,
                details=f'seed entry {i}',
                created_at=NOW - timedelta(hours=30 - i),
            ))
        await session.commit()

        sq_list = [
            ('月度销售汇总', '上个月的销售总额是多少', 'SELECT SUM(total_amount) as gmv FROM orders', DS_1),
            ('各部门人数', '每个部门的员工数量', 'SELECT department, COUNT(*) FROM users GROUP BY department', DS_1),
            ('Top 10 商品', '销量最高的 10 个商品', 'SELECT p.name, SUM(oi.quantity) FROM order_items oi JOIN products p GROUP BY p.name LIMIT 10', DS_1),
            ('城市订单分布', '各城市的订单分布', 'SELECT city, COUNT(*) FROM orders GROUP BY city', DS_1),
            ('本月新增用户', '本月新注册用户数', 'SELECT COUNT(*) FROM users', DS_1),
            ('支付成功率', '支付成功的订单占比', 'SELECT COUNT(*) as rate FROM payments', DS_1),
            ('类目销售占比', '各类目的销售占比', 'SELECT p.category, SUM(qty*price) FROM order_items JOIN products GROUP BY p.category', DS_1),
            ('平均配送时长', '平均发货时长', 'SELECT AVG(TIMESTAMPDIFF(HOUR, o.created_at, s.shipped_at)) FROM orders o JOIN shipping s ON o.id = s.order_id', DS_1),
            ('用户复购率', '用户复购率', 'SELECT COUNT(*) as rate FROM orders', DS_1),
            ('好评率', '好评率(4星以上)', 'SELECT SUM(CASE WHEN rating >= 4 THEN 1 ELSE 0 END) FROM reviews', DS_1),
            ('Demo 查询 1', 'Demo 数据查询', 'SELECT * FROM demo_table LIMIT 10', DS_2),
            ('Demo 查询 2', 'Demo 统计', 'SELECT COUNT(*) FROM demo_table', DS_2),
            ('活跃用户数', '最近 30 天活跃用户', 'SELECT COUNT(DISTINCT user_id) FROM orders', DS_1),
            ('GMV 趋势', '最近 6 个月 GMV 趋势', 'SELECT SUM(total_amount) FROM orders', DS_1),
            ('库存预警', '库存不足 100 的商品', 'SELECT name, stock FROM products WHERE stock < 100', DS_1),
        ]
        for i, (name, qt, sql, ds_id) in enumerate(sq_list):
            user_id = [USER_ADMIN, USER_ANALYST, USER_VIEWER][i % 3]
            session.add(SavedQuery(
                id=uuid.uuid4(), tenant_id=TENANT_1, user_id=user_id,
                name=name, query_text=qt, generated_sql=sql, datasource_id=ds_id,
                created_at=NOW - timedelta(hours=15 - i),
            ))
        await session.commit()

        fb_items = [('up', '结果准确'), ('up', '数据正确'), ('down', 'SQL 有误'),
                    ('up', '图表智能'), ('up', '速度快'), ('down', '字段不匹配'),
                    ('up', '解决问题'), ('up', '方便'), ('down', '表结构没扫描'),
                    ('up', '推荐'), ('up', '准确'), ('up', '好用')]
        qids = [str(uuid.uuid4()) for _ in range(12)]
        for i, (rating, comment) in enumerate(fb_items):
            user_id = [USER_ADMIN, USER_ANALYST, USER_VIEWER, USER_OPS][i % 4]
            session.add(Feedback(
                id=uuid.uuid4(), tenant_id=TENANT_1, user_id=user_id,
                query_id=qids[i], rating=rating, comment=comment,
                created_at=NOW - timedelta(hours=12 - i),
            ))
        await session.commit()

        event_names = [
            'EVENT_USER_LOGIN', 'EVENT_QUERY_EXECUTE', 'EVENT_QUERY_SUCCESS',
            'EVENT_DATASOURCE_CREATE', 'EVENT_DATASOURCE_SCAN', 'EVENT_CHART_VIEW',
            'EVENT_QUERY_SAVE', 'EVENT_FEEDBACK_SUBMIT', 'EVENT_FIRST_USE_COMPLETE',
            'EVENT_QUERY_ERROR', 'EVENT_CHART_TYPE_CHANGE', 'EVENT_QUERY_EXPORT_CSV',
            'EVENT_DATASOURCE_TEST', 'EVENT_USER_LOGOUT',
        ]
        for i in range(40):
            user_id = [USER_ADMIN, USER_ANALYST, USER_VIEWER, USER_OPS][i % 4]
            session.add(AnalyticsEvent(
                id=uuid.uuid4(), tenant_id=TENANT_1, user_id=user_id,
                event_name=event_names[i % len(event_names)],
                event_data=json.dumps({'index': i}),
                created_at=NOW - timedelta(hours=40 - i),
            ))
        await session.commit()

        convs = [
            ('销售分析', DS_1, [{'role': 'user', 'content': '上个月的销售总额是多少？'}, {'role': 'assistant', 'sql': 'SELECT SUM(total_amount) FROM orders', 'success': True}, {'role': 'user', 'content': '按城市分组呢？'}, {'role': 'assistant', 'sql': 'SELECT city, SUM(total_amount) GROUP BY city', 'success': True}]),
            ('用户分析', DS_1, [{'role': 'user', 'content': '最近 30 天新增用户数'}, {'role': 'assistant', 'sql': 'SELECT COUNT(*) FROM users', 'success': True}]),
            ('商品分析', DS_1, [{'role': 'user', 'content': '销量 Top 10 商品'}, {'role': 'assistant', 'sql': 'SELECT p.name FROM products LIMIT 10', 'success': True}]),
            ('支付分析', DS_1, [{'role': 'user', 'content': '支付成功率'}, {'role': 'assistant', 'sql': 'SELECT COUNT(*) FROM payments', 'success': True}]),
            ('Demo 查询', DS_2, [{'role': 'user', 'content': 'Demo 数据有多少条'}, {'role': 'assistant', 'sql': 'SELECT COUNT(*) FROM demo_table', 'success': True}]),
        ]
        for title, ds_id, messages in convs:
            session.add(Conversation(
                id=uuid.uuid4(), tenant_id=TENANT_1, user_id=USER_ADMIN,
                title=title, datasource_id=str(ds_id),
                messages=json.dumps(messages, ensure_ascii=False),
                created_at=NOW - timedelta(hours=5), updated_at=NOW,
            ))
        await session.commit()

        print('Seed completed successfully:')
        print(f'  Tenants:            {len(tenants)}')
        print(f'  Users:              {len(users)}')
        print(f'  DataSources:        {len(datasources)}')
        print(f'  MetadataConfigs:    {len(metadata_configs)}')
        print(f'  AuditLogs:          30')
        print(f'  SavedQueries:       {len(sq_list)}')
        print(f'  Feedback:           {len(fb_items)}')
        print(f'  AnalyticsEvents:    40')
        print(f'  Conversations:      {len(convs)}')


if __name__ == '__main__':
    asyncio.run(seed())
