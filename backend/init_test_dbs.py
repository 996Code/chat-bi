"""
Unified test database initializer for MySQL and PostgreSQL.
Creates identical e-commerce business data in both databases.

Usage:
  .venv/bin/python init_test_dbs.py
  .venv/bin/python init_test_dbs.py --mysql-only
  .venv/bin/python init_test_dbs.py --pg-only

Environment variables:
  MYSQL_HOST=192.168.3.110     (default)
  MYSQL_PORT=3306              (default)
  MYSQL_USER=root              (default)
  MYSQL_PASS=yjt_mysql         (default)
  MYSQL_DB=chatbi_test         (default)

  PG_HOST=192.168.3.110        (default)
  PG_PORT=5432                 (default)
  PG_USER=postgres             (default)
  PG_PASS=postgres             (default)
  PG_DB=chatbi_test            (default)
"""
import argparse
import asyncio
import os
import random
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


# ===== Environment =====
MYSQL_HOST = os.environ.get('MYSQL_HOST', '192.168.3.110')
MYSQL_PORT = os.environ.get('MYSQL_PORT', '3306')
MYSQL_USER = os.environ.get('MYSQL_USER', 'root')
MYSQL_PASS = os.environ.get('MYSQL_PASS', 'yjt_mysql')
MYSQL_DB = os.environ.get('MYSQL_DB', 'chatbi_test')

PG_HOST = os.environ.get('PG_HOST', '192.168.3.110')
PG_PORT = os.environ.get('PG_PORT', '5432')
PG_USER = os.environ.get('PG_USER', 'postgres')
PG_PASS = os.environ.get('PG_PASS', 'postgres')
PG_DB = os.environ.get('PG_DB', 'chatbi_test')


CITIES = ['北京', '上海', '广州', '深圳', '杭州', '成都', '武汉', '南京', '重庆', '西安',
          '长沙', '郑州', '天津', '苏州', '青岛', '大连', '厦门', '福州', '合肥', '昆明',
          '宁波', '无锡', '济南', '哈尔滨', '温州', '东莞', '佛山', '珠海', '南通', '烟台']
PROVINCES = {
    '北京': '北京', '上海': '上海', '广州': '广东', '深圳': '广东', '杭州': '浙江',
    '成都': '四川', '武汉': '湖北', '南京': '江苏', '重庆': '重庆', '西安': '陕西',
    '长沙': '湖南', '郑州': '河南', '天津': '天津', '苏州': '江苏', '青岛': '山东',
    '大连': '辽宁', '厦门': '福建', '福州': '福建', '合肥': '安徽', '昆明': '云南',
    '宁波': '浙江', '无锡': '江苏', '济南': '山东', '哈尔滨': '黑龙江', '温州': '浙江',
    '东莞': '广东', '佛山': '广东', '珠海': '广东', '南通': '江苏', '烟台': '山东',
}

ORDER_STATUSES = ['pending', 'paid', 'processing', 'shipped', 'delivered', 'completed', 'cancelled', 'refunded', 'returning']
PAYMENT_METHODS = ['alipay', 'wechat_pay', 'credit_card', 'debit_card', 'bank_transfer', 'apple_pay', 'huabei']
CARRIERS = ['顺丰速运', '中通快递', '圆通速递', '韵达快递', '百世快递', '申通快递', '京东物流', '德邦快递', '邮政EMS']
RETURN_REASONS = ['商品损坏', '发错货', '质量问题', '不喜欢', '尺寸不合', '描述不符', '多买了', '价格变化']

CATEGORIES = [
    ('数码电子', ['智能手机', '笔记本电脑', '平板电脑', '蓝牙耳机', '智能手表', '充电宝', '蓝牙音箱', '机械键盘', '显示器', '路由器']),
    ('服装鞋包', ['男装', '女装', '童装', '运动鞋', '休闲鞋', '男包', '女包', '手表', '太阳镜', '围巾帽子']),
    ('家居家装', ['沙发', '床垫', '餐桌椅', '衣柜', '书架', '地毯', '窗帘', '台灯', '收纳箱', '四件套']),
    ('食品生鲜', ['坚果零食', '茶叶饮品', '新鲜水果', '肉禽蛋品', '海鲜水产', '米面粮油', '乳制品', '调味酱料']),
    ('运动户外', ['跑步机', '瑜伽用品', '自行车', '登山鞋', '帐篷', '篮球', '游泳装备', '健身器材']),
    ('美妆个护', ['面部护肤', '彩妆', '洗护用品', '香水', '男士护理', '美妆工具', '防晒用品']),
    ('母婴玩具', ['婴儿推车', '奶粉辅食', '纸尿裤', '儿童玩具', '积木拼插', '绘本早教', '安全座椅']),
    ('图书文具', ['小说文学', '编程技术', '经济管理', '少儿绘本', '办公文具', '钢笔墨水']),
]

PRODUCT_DATA = {
    '智能手机': [
        ('iPhone 15 Pro', 8999, 6200), ('iPhone 15', 5999, 4100), ('华为 Mate 60 Pro', 6999, 4800),
        ('华为 P60 Pro', 5499, 3800), ('小米14 Ultra', 5999, 4200), ('小米14', 3999, 2800),
        ('OPPO Find X7 Ultra', 5999, 4100), ('vivo X100 Pro', 4999, 3500), ('三星 Galaxy S24 Ultra', 9999, 7000),
        ('荣耀 Magic6 Pro', 5499, 3800), ('一加 12', 4299, 3000), ('realme GT5 Pro', 3399, 2400),
    ],
    '笔记本电脑': [
        ('MacBook Pro 14', 14999, 10500), ('MacBook Air M3', 8999, 6300),
        ('ThinkPad X1 Carbon', 12999, 9100), ('联想拯救者 Y9000P', 8499, 6000),
        ('华为 MateBook X Pro', 9999, 7000), ('戴尔 XPS 15', 11999, 8400),
        ('惠普战99', 6999, 4900), ('华硕 ROG 幻16', 13999, 9800),
    ],
    '平板电脑': [('iPad Air', 4799, 3300), ('华为 MatePad Pro', 3299, 2300), ('小米平板6', 1999, 1400)],
    '蓝牙耳机': [('AirPods Pro', 1799, 600), ('华为 FreeBuds', 999, 320), ('小米 Buds', 399, 120), ('索尼 WF-1000XM5', 1999, 700)],
    '智能手表': [('Apple Watch S9', 2999, 1100), ('华为 Watch GT4', 1499, 500), ('小米手表', 799, 250)],
    '充电宝': [('小米 20000mAh', 129, 38), ('Anker 10000mAh', 99, 28), ('华为 超级快充', 199, 60)],
    '蓝牙音箱': [('JBL Flip 6', 699, 210), ('Bose SoundLink', 999, 300), ('索尼 SRS-XB', 799, 240)],
    '机械键盘': [('Cherry MX', 899, 270), ('罗技 K845', 399, 120), ('雷蛇 黑寡妇', 699, 210)],
    '男装': [
        ('商务修身衬衫', 299, 80), ('休闲POLO衫', 199, 55), ('纯棉T恤', 99, 28),
        ('牛仔裤-直筒', 359, 95), ('运动卫衣', 259, 70), ('羊毛大衣', 1299, 380),
        ('轻薄羽绒服', 699, 200), ('西装外套', 899, 260),
    ],
    '女装': [
        ('碎花连衣裙', 399, 110), ('真丝衬衫', 599, 170), ('高腰阔腿裤', 299, 80),
        ('针织开衫', 359, 100), ('羽绒服-轻薄', 899, 260), ('羊绒围巾', 459, 130),
    ],
    '运动鞋': [
        ('Air Max 跑步鞋', 1299, 380), ('Ultra Boost 跑鞋', 1199, 350),
        ('AJ1 篮球鞋', 1099, 320), ('New Balance 990', 1699, 500),
        ('李宁 赤兔6', 399, 115), ('安踏 创跑鞋', 299, 85),
    ],
    '坚果零食': [
        ('三只松鼠 混合坚果 500g', 89, 32), ('良品铺子 每日坚果 30包', 109, 38),
        ('洽洽 瓜子 308g', 12.9, 4), ('百草味 芒果干 100g', 9.9, 3),
        ('乐事 薯片组合 6桶', 39.9, 14), ('费列罗 巧克力 24粒', 99, 35),
    ],
    '茶叶饮品': [
        ('西湖龙井 明前特级 100g', 299, 95), ('云南普洱 熟茶饼 357g', 199, 65),
        ('安溪铁观音 250g', 159, 50), ('立顿 红茶 100包', 39.9, 12),
        ('雀巢 速溶咖啡 100条', 89, 28), ('元气森林 气泡水 12瓶', 49.9, 18),
    ],
    '新鲜水果': [
        ('智利车厘子 5斤', 199, 95), ('泰国金枕榴莲 1个', 129, 55),
        ('海南芒果 5斤', 59, 25), ('阳光玫瑰葡萄 3斤', 89, 38),
        ('新疆阿克苏苹果 5斤', 49, 20), ('赣南脐橙 5斤', 39, 16),
    ],
    '沙发': [
        ('北欧简约布艺沙发 三人位', 3999, 1200), ('意式真皮沙发 L型', 8999, 2700),
        ('日式实木沙发组合', 5499, 1650), ('电动功能沙发 单人', 2999, 900),
    ],
    '跑步机': [
        ('家用折叠跑步机', 2999, 900), ('商用跑步机', 9999, 3000),
        ('超静音折叠跑步机', 1999, 600), ('走步机 家用款', 999, 300),
    ],
}

DEFAULT_PRODUCTS = [
    ('T恤-基础款', 59, 16), ('水杯-保温500ml', 89, 25), ('毛巾-纯棉', 29, 8),
    ('拖鞋-居家', 39, 11), ('雨伞-自动', 69, 20), ('充电宝-10000mAh', 79, 23),
    ('手机壳-硅胶', 39, 10), ('数据线-TypeC', 19, 5), ('笔记本-A5', 15, 4),
    ('中性笔-0.5mm', 5, 1), ('垃圾袋-100只', 19, 5), ('洗洁精-1kg', 12, 3),
]

BRANDS = [
    'Apple', '华为 Huawei', '小米 Xiaomi', 'OPPO', 'vivo', '三星 Samsung', '荣耀 Honor', '一加 OnePlus',
    '联想 Lenovo', '戴尔 Dell', '惠普 HP', '华硕 ASUS', 'ThinkPad', '耐克 Nike', '阿迪达斯 Adidas',
    '安踏 Anta', '李宁 Li-Ning', '优衣库 Uniqlo', '三只松鼠', '良品铺子', '百草味',
    '西湖龙井', '立顿 Lipton', '雀巢 Nestle', '海尔 Haier', '美的 Midea', '戴森 Dyson',
    '宜家 IKEA', '全友家居', '顾家家居', '李宁运动', '迪卡侬 Decathlon', '费列罗 Ferrero',
]


# ===== DDL =====
def get_ddl_mysql(db_name):
    return f"""
    CREATE DATABASE IF NOT EXISTS {db_name} DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    """

TABLE_DDL_MYSQL = [
    ('t_users', '''
        CREATE TABLE t_users (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(100) NOT NULL,
            email VARCHAR(255),
            phone VARCHAR(20),
            city VARCHAR(100),
            province VARCHAR(50),
            vip_level INT DEFAULT 0 COMMENT '0=普通,1=银卡,2=金卡,3=钻石,4=黑金',
            points INT DEFAULT 0,
            total_orders INT DEFAULT 0,
            total_spent DECIMAL(12,2) DEFAULT 0,
            last_login DATETIME,
            created_at DATETIME NOT NULL DEFAULT NOW(),
            updated_at DATETIME NOT NULL DEFAULT NOW(),
            INDEX idx_city (city), INDEX idx_vip (vip_level), INDEX idx_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''),
    ('t_addresses', '''
        CREATE TABLE t_addresses (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            name VARCHAR(100) NOT NULL,
            phone VARCHAR(20) NOT NULL,
            province VARCHAR(50) NOT NULL,
            city VARCHAR(100) NOT NULL,
            district VARCHAR(100),
            detail VARCHAR(500) NOT NULL,
            is_default TINYINT(1) DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT NOW(),
            INDEX idx_user (user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''),
    ('t_categories', '''
        CREATE TABLE t_categories (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            parent_id BIGINT DEFAULT NULL,
            icon VARCHAR(500),
            sort_order INT DEFAULT 0,
            is_active TINYINT(1) DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT NOW(),
            INDEX idx_parent (parent_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''),
    ('t_brands', '''
        CREATE TABLE t_brands (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            logo VARCHAR(500),
            description TEXT,
            is_active TINYINT(1) DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT NOW()
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''),
    ('t_products', '''
        CREATE TABLE t_products (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            category_id BIGINT NOT NULL,
            brand_id BIGINT DEFAULT NULL,
            price DECIMAL(10,2) NOT NULL,
            cost_price DECIMAL(10,2) NOT NULL,
            market_price DECIMAL(10,2),
            stock INT NOT NULL DEFAULT 0,
            weight DECIMAL(8,2),
            sku VARCHAR(50) UNIQUE,
            barcode VARCHAR(50),
            status VARCHAR(20) DEFAULT 'active',
            tags VARCHAR(500),
            created_at DATETIME NOT NULL DEFAULT NOW(),
            updated_at DATETIME NOT NULL DEFAULT NOW(),
            INDEX idx_category (category_id), INDEX idx_brand (brand_id),
            INDEX idx_status (status), INDEX idx_price (price)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''),
    ('t_orders', '''
        CREATE TABLE t_orders (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            order_no VARCHAR(50) UNIQUE NOT NULL,
            total_amount DECIMAL(12,2) NOT NULL,
            discount_amount DECIMAL(10,2) DEFAULT 0,
            shipping_fee DECIMAL(10,2) DEFAULT 0,
            coupon_code VARCHAR(50),
            status VARCHAR(20) NOT NULL,
            city VARCHAR(100),
            province VARCHAR(50),
            address_id BIGINT,
            note VARCHAR(500),
            cancel_reason VARCHAR(200),
            created_at DATETIME NOT NULL DEFAULT NOW(),
            paid_at DATETIME,
            shipped_at DATETIME,
            delivered_at DATETIME,
            completed_at DATETIME,
            INDEX idx_user (user_id), INDEX idx_status (status),
            INDEX idx_created (created_at), INDEX idx_order_no (order_no)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''),
    ('t_order_items', '''
        CREATE TABLE t_order_items (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            order_id BIGINT NOT NULL,
            product_id BIGINT NOT NULL,
            product_name VARCHAR(200),
            quantity INT NOT NULL,
            price DECIMAL(10,2) NOT NULL,
            subtotal DECIMAL(10,2) NOT NULL,
            INDEX idx_order (order_id), INDEX idx_product (product_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''),
    ('t_payments', '''
        CREATE TABLE t_payments (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            order_id BIGINT NOT NULL,
            amount DECIMAL(10,2) NOT NULL,
            method VARCHAR(50) NOT NULL,
            transaction_id VARCHAR(100),
            status VARCHAR(20) DEFAULT 'success',
            paid_at DATETIME NOT NULL DEFAULT NOW(),
            refund_amount DECIMAL(10,2) DEFAULT 0,
            INDEX idx_order (order_id), INDEX idx_method (method),
            INDEX idx_txn (transaction_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''),
    ('t_shipping', '''
        CREATE TABLE t_shipping (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            order_id BIGINT NOT NULL,
            carrier VARCHAR(100),
            tracking_no VARCHAR(100),
            shipped_at DATETIME NOT NULL DEFAULT NOW(),
            delivered_at DATETIME,
            status VARCHAR(20) DEFAULT 'in_transit',
            INDEX idx_order (order_id), INDEX idx_tracking (tracking_no)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''),
    ('t_reviews', '''
        CREATE TABLE t_reviews (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            order_id BIGINT NOT NULL,
            product_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            rating INT NOT NULL,
            content VARCHAR(1000),
            images INT DEFAULT 0,
            is_anonymous TINYINT(1) DEFAULT 0,
            helpful_count INT DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT NOW(),
            INDEX idx_order (order_id), INDEX idx_product (product_id),
            INDEX idx_rating (rating), INDEX idx_user (user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''),
    ('t_returns', '''
        CREATE TABLE t_returns (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            order_id BIGINT NOT NULL,
            order_item_id BIGINT,
            user_id BIGINT NOT NULL,
            reason VARCHAR(200),
            description TEXT,
            refund_amount DECIMAL(10,2) NOT NULL,
            status VARCHAR(20) DEFAULT 'pending',
            carrier VARCHAR(100),
            tracking_no VARCHAR(100),
            created_at DATETIME NOT NULL DEFAULT NOW(),
            approved_at DATETIME,
            completed_at DATETIME,
            INDEX idx_order (order_id), INDEX idx_user (user_id),
            INDEX idx_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''),
    ('t_coupons', '''
        CREATE TABLE t_coupons (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            code VARCHAR(50) UNIQUE NOT NULL,
            name VARCHAR(200),
            discount_type VARCHAR(20) NOT NULL,
            discount_value DECIMAL(10,2) NOT NULL,
            min_order DECIMAL(10,2) DEFAULT 0,
            max_uses INT DEFAULT NULL,
            used_count INT DEFAULT 0,
            per_user_limit INT DEFAULT 1,
            valid_from DATETIME NOT NULL,
            valid_until DATETIME NOT NULL,
            is_active TINYINT(1) DEFAULT 1,
            INDEX idx_code (code), INDEX idx_active (is_active)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''),
    ('t_user_coupons', '''
        CREATE TABLE t_user_coupons (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            coupon_id BIGINT NOT NULL,
            status VARCHAR(20) DEFAULT 'unused',
            used_at DATETIME,
            created_at DATETIME NOT NULL DEFAULT NOW(),
            INDEX idx_user (user_id), INDEX idx_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''),
    ('t_warehouses', '''
        CREATE TABLE t_warehouses (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            city VARCHAR(100),
            province VARCHAR(50),
            capacity INT,
            address VARCHAR(500),
            manager_name VARCHAR(100),
            manager_phone VARCHAR(20),
            is_active TINYINT(1) DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT NOW()
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''),
    ('t_inventory', '''
        CREATE TABLE t_inventory (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            product_id BIGINT NOT NULL,
            warehouse_id BIGINT NOT NULL,
            quantity INT NOT NULL DEFAULT 0,
            reserved INT NOT NULL DEFAULT 0,
            min_stock INT DEFAULT 10,
            updated_at DATETIME NOT NULL DEFAULT NOW(),
            UNIQUE KEY uk_product_warehouse (product_id, warehouse_id),
            INDEX idx_product (product_id), INDEX idx_warehouse (warehouse_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''),
]

TABLE_DDL_PG = [
    ('t_users', '''
        CREATE TABLE t_users (
            id BIGSERIAL PRIMARY KEY,
            username VARCHAR(100) NOT NULL,
            email VARCHAR(255),
            phone VARCHAR(20),
            city VARCHAR(100),
            province VARCHAR(50),
            vip_level INT DEFAULT 0,
            points INT DEFAULT 0,
            total_orders INT DEFAULT 0,
            total_spent DECIMAL(12,2) DEFAULT 0,
            last_login TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )'''),
    ('t_addresses', '''
        CREATE TABLE t_addresses (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            name VARCHAR(100) NOT NULL,
            phone VARCHAR(20) NOT NULL,
            province VARCHAR(50) NOT NULL,
            city VARCHAR(100) NOT NULL,
            district VARCHAR(100),
            detail VARCHAR(500) NOT NULL,
            is_default SMALLINT DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )'''),
    ('t_categories', '''
        CREATE TABLE t_categories (
            id BIGSERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            parent_id BIGINT DEFAULT NULL,
            icon VARCHAR(500),
            sort_order INT DEFAULT 0,
            is_active SMALLINT DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )'''),
    ('t_brands', '''
        CREATE TABLE t_brands (
            id BIGSERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            logo VARCHAR(500),
            description TEXT,
            is_active SMALLINT DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )'''),
    ('t_products', '''
        CREATE TABLE t_products (
            id BIGSERIAL PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            category_id BIGINT NOT NULL,
            brand_id BIGINT DEFAULT NULL,
            price DECIMAL(10,2) NOT NULL,
            cost_price DECIMAL(10,2) NOT NULL,
            market_price DECIMAL(10,2),
            stock INT NOT NULL DEFAULT 0,
            weight DECIMAL(8,2),
            sku VARCHAR(50) UNIQUE,
            barcode VARCHAR(50),
            status VARCHAR(20) DEFAULT 'active',
            tags VARCHAR(500),
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )'''),
    ('t_orders', '''
        CREATE TABLE t_orders (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            order_no VARCHAR(50) UNIQUE NOT NULL,
            total_amount DECIMAL(12,2) NOT NULL,
            discount_amount DECIMAL(10,2) DEFAULT 0,
            shipping_fee DECIMAL(10,2) DEFAULT 0,
            coupon_code VARCHAR(50),
            status VARCHAR(20) NOT NULL,
            city VARCHAR(100),
            province VARCHAR(50),
            address_id BIGINT,
            note VARCHAR(500),
            cancel_reason VARCHAR(200),
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            paid_at TIMESTAMP,
            shipped_at TIMESTAMP,
            delivered_at TIMESTAMP,
            completed_at TIMESTAMP
        )'''),
    ('t_order_items', '''
        CREATE TABLE t_order_items (
            id BIGSERIAL PRIMARY KEY,
            order_id BIGINT NOT NULL,
            product_id BIGINT NOT NULL,
            product_name VARCHAR(200),
            quantity INT NOT NULL,
            price DECIMAL(10,2) NOT NULL,
            subtotal DECIMAL(10,2) NOT NULL
        )'''),
    ('t_payments', '''
        CREATE TABLE t_payments (
            id BIGSERIAL PRIMARY KEY,
            order_id BIGINT NOT NULL,
            amount DECIMAL(10,2) NOT NULL,
            method VARCHAR(50) NOT NULL,
            transaction_id VARCHAR(100),
            status VARCHAR(20) DEFAULT 'success',
            paid_at TIMESTAMP NOT NULL DEFAULT NOW(),
            refund_amount DECIMAL(10,2) DEFAULT 0
        )'''),
    ('t_shipping', '''
        CREATE TABLE t_shipping (
            id BIGSERIAL PRIMARY KEY,
            order_id BIGINT NOT NULL,
            carrier VARCHAR(100),
            tracking_no VARCHAR(100),
            shipped_at TIMESTAMP NOT NULL DEFAULT NOW(),
            delivered_at TIMESTAMP,
            status VARCHAR(20) DEFAULT 'in_transit'
        )'''),
    ('t_reviews', '''
        CREATE TABLE t_reviews (
            id BIGSERIAL PRIMARY KEY,
            order_id BIGINT NOT NULL,
            product_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            rating INT NOT NULL,
            content VARCHAR(1000),
            images INT DEFAULT 0,
            is_anonymous SMALLINT DEFAULT 0,
            helpful_count INT DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )'''),
    ('t_returns', '''
        CREATE TABLE t_returns (
            id BIGSERIAL PRIMARY KEY,
            order_id BIGINT NOT NULL,
            order_item_id BIGINT,
            user_id BIGINT NOT NULL,
            reason VARCHAR(200),
            description TEXT,
            refund_amount DECIMAL(10,2) NOT NULL,
            status VARCHAR(20) DEFAULT 'pending',
            carrier VARCHAR(100),
            tracking_no VARCHAR(100),
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            approved_at TIMESTAMP,
            completed_at TIMESTAMP
        )'''),
    ('t_coupons', '''
        CREATE TABLE t_coupons (
            id BIGSERIAL PRIMARY KEY,
            code VARCHAR(50) UNIQUE NOT NULL,
            name VARCHAR(200),
            discount_type VARCHAR(20) NOT NULL,
            discount_value DECIMAL(10,2) NOT NULL,
            min_order DECIMAL(10,2) DEFAULT 0,
            max_uses INT DEFAULT NULL,
            used_count INT DEFAULT 0,
            per_user_limit INT DEFAULT 1,
            valid_from TIMESTAMP NOT NULL,
            valid_until TIMESTAMP NOT NULL,
            is_active SMALLINT DEFAULT 1
        )'''),
    ('t_user_coupons', '''
        CREATE TABLE t_user_coupons (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            coupon_id BIGINT NOT NULL,
            status VARCHAR(20) DEFAULT 'unused',
            used_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )'''),
    ('t_warehouses', '''
        CREATE TABLE t_warehouses (
            id BIGSERIAL PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            city VARCHAR(100),
            province VARCHAR(50),
            capacity INT,
            address VARCHAR(500),
            manager_name VARCHAR(100),
            manager_phone VARCHAR(20),
            is_active SMALLINT DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )'''),
    ('t_inventory', '''
        CREATE TABLE t_inventory (
            id BIGSERIAL PRIMARY KEY,
            product_id BIGINT NOT NULL,
            warehouse_id BIGINT NOT NULL,
            quantity INT NOT NULL DEFAULT 0,
            reserved INT NOT NULL DEFAULT 0,
            min_stock INT DEFAULT 10,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE (product_id, warehouse_id)
        )'''),
]


async def seed_database(conn, is_mysql: bool, base_time):
    """Seed data into an existing database. conn is an async connection."""
    # PG counter for lastrowid emulation (PG SERIAL auto-increments from 1)
    _pg_id = [0]
    def next_id(r):
        _pg_id[0] += 1
        return r.lastrowid if is_mysql else _pg_id[0]

    first_names = ['张', '李', '王', '赵', '刘', '陈', '杨', '黄', '周', '吴',
                   '徐', '孙', '马', '朱', '胡', '郭', '林', '何', '高', '梁']
    last_names = ['伟', '芳', '娜', '敏', '静', '丽', '强', '磊', '军', '洋',
                  '勇', '艳', '杰', '涛', '明', '超', '秀英', '霞', '平', '刚']
    districts = ['朝阳区', '海淀区', '浦东新区', '黄浦区', '天河区', '福田区',
                 '西湖区', '武侯区', '江岸区', '玄武区', '渝中区', '雁塔区']

    # --- Users ---
    user_ids = []
    for i in range(500):
        name = random.choice(first_names) + random.choice(last_names)
        city = random.choice(CITIES)
        province = PROVINCES.get(city, '')
        email = f'user{i:04d}@test.com'
        phone = f'1{random.randint(3,9)}{random.randint(100000000,999999999)}'
        vip = random.choices([0, 1, 2, 3, 4], weights=[50, 25, 15, 7, 3])[0]
        points = random.randint(0, 5000)
        n_orders = random.randint(0, 30)
        total_spent = round(n_orders * random.uniform(100, 2000), 2)
        last_login = base_time + timedelta(days=random.randint(60, 179))
        created = base_time + timedelta(days=random.randint(0, 120))
        r = await conn.execute(
            text("INSERT INTO t_users (username, email, phone, city, province, vip_level, points, "
                 "total_orders, total_spent, last_login, created_at) "
                 "VALUES (:u, :e, :p, :c, :pr, :v, :pts, :no, :ts, :ll, :ca)"),
            {'u': name, 'e': email, 'p': phone, 'c': city, 'pr': province, 'v': vip,
             'pts': points, 'no': n_orders, 'ts': total_spent, 'll': last_login, 'ca': created})
        user_ids.append(next_id(r))
        # For PG, lastrowid doesn't work; we'll use a counter
    # For PG, re-fetch IDs
    if not is_mysql:
        r = await conn.execute(text("SELECT id FROM t_users ORDER BY id"))
        user_ids = [row[0] for row in r.fetchall()]

    # --- Addresses ---
    for uid in user_ids[:300]:
        r = await conn.execute(text("SELECT city, province, username FROM t_users WHERE id = :id"), {'id': uid})
        row = r.fetchone()
        city, province, uname = row
        for j in range(random.randint(1, 3)):
            detail = f'{random.choice(districts)}{random.choice(["路","街"])}{random.randint(1,999)}号{random.randint(101,2802)}室'
            await conn.execute(
                text("INSERT INTO t_addresses (user_id, name, phone, province, city, district, detail, is_default) "
                     "VALUES (:uid, :n, :p, :pr, :c, :d, :dt, :df)"),
                {'uid': uid, 'n': uname, 'p': f'1{random.randint(3,9)}{random.randint(100000000,999999999)}',
                 'pr': province or '', 'c': city, 'd': random.choice(districts), 'dt': detail, 'df': 1 if j == 0 else 0})

    # --- Brands ---
    brand_ids = []
    for b in BRANDS:
        r = await conn.execute(text("INSERT INTO t_brands (name, is_active) VALUES (:n, 1)"), {'n': b})
        brand_ids.append(next_id(r))
    if not is_mysql:
        r = await conn.execute(text("SELECT id FROM t_brands ORDER BY id"))
        brand_ids = [row[0] for row in r.fetchall()]

    # --- Categories ---
    cat_ids = {}
    for parent_name, children in CATEGORIES:
        r = await conn.execute(
            text("INSERT INTO t_categories (name, parent_id, sort_order, is_active) VALUES (:n, NULL, 0, 1)"),
            {'n': parent_name})
        pid = next_id(r)
        cat_ids[parent_name] = pid
        for j, child in enumerate(children):
            r = await conn.execute(
                text("INSERT INTO t_categories (name, parent_id, sort_order, is_active) VALUES (:n, :p, :so, 1)"),
                {'n': child, 'p': pid, 'so': j + 1})
            cat_ids[child] = next_id(r)

    # --- Products ---
    product_cat_ids = {k: v for k, v in cat_ids.items() if k not in [c[0] for c in CATEGORIES]}
    brand_mapping = {
        '智能手机': [0,1,2,3,4,5,6,7], '笔记本电脑': [8,9,10,11,12],
        '平板电脑': [0,1,2], '蓝牙耳机': [0,1,2,13], '智能手表': [0,1,2],
        '充电宝': [2,24,1], '蓝牙音箱': [13,26,5], '机械键盘': [14,10,25],
        '男装': [17,15,16], '女装': [17], '运动鞋': [13,14,15,16],
        '坚果零食': [18,19,20], '茶叶饮品': [21,22,23], '新鲜水果': [],
        '沙发': [28,29], '跑步机': [],
    }
    product_ids = []
    sku = 1
    for cat_name, items in PRODUCT_DATA.items():
        cid = cat_ids.get(cat_name)
        if not cid:
            continue
        b_ids = brand_mapping.get(cat_name, [])
        for name, price, cost in items:
            bid = brand_ids[random.choice(b_ids)] if b_ids else None
            market = round(price * random.uniform(1.2, 1.8), 2)
            stock = random.randint(5, 800)
            weight = round(random.uniform(0.05, 15.0), 2)
            tags = random.sample(['新品', '热销', '特价', '限时折扣', '包邮', '官方正品'], k=random.randint(0, 3))
            r = await conn.execute(
                text("INSERT INTO t_products (name, category_id, brand_id, price, cost_price, market_price, "
                     "stock, weight, sku, status, tags) "
                     "VALUES (:n, :c, :b, :p, :cp, :mp, :s, :w, :sk, 'active', :t)"),
                {'n': name, 'c': cid, 'b': bid, 'p': price, 'cp': cost, 'mp': market,
                 's': stock, 'w': weight, 'sk': f'SKU{sku:06d}',
                 't': ','.join(tags) if tags else ''})
            product_ids.append(next_id(r))
            sku += 1

    for name, price, cost in DEFAULT_PRODUCTS:
        cid = random.choice(list(product_cat_ids.values()))
        r = await conn.execute(
            text("INSERT INTO t_products (name, category_id, price, cost_price, market_price, stock, sku, status) "
                 "VALUES (:n, :c, :p, :cp, :mp, :s, :sk, 'active')"),
            {'n': name, 'c': cid, 'p': price, 'cp': cost,
             'mp': round(price * 1.5, 2), 's': random.randint(100, 500), 'sk': f'SKU{sku:06d}'})
        product_ids.append(next_id(r))
        sku += 1

    if not is_mysql:
        r = await conn.execute(text("SELECT id FROM t_products ORDER BY id"))
        product_ids = [row[0] for row in r.fetchall()]

    # --- Orders ---
    order_ids = []
    order_user_ids = []
    for i in range(2000):
        uid = random.choice(user_ids)
        day_offset = random.randint(0, 179)
        order_no = f'ORD{base_time.year}{(base_time + timedelta(days=day_offset)).strftime("%m%d%H%M%S")}{i:06d}'
        total = round(random.uniform(29.9, 25000), 2)
        discount = round(random.uniform(0, total * 0.3), 2)
        shipping = random.choice([0, 0, 0, 5.9, 9.9, 15.0, 20.0])
        status = random.choices(ORDER_STATUSES, weights=[5, 15, 10, 15, 20, 15, 8, 7, 5])[0]
        city = random.choice(CITIES)
        province = PROVINCES.get(city, '')
        note = random.choice(['', '', '', '', '请尽快发货', '周末不在家', '送前电话联系', '需要发票'])
        created = base_time + timedelta(days=day_offset, hours=random.randint(8, 22))
        paid = created + timedelta(minutes=random.randint(1, 30)) if status not in ('pending', 'cancelled') else None
        shipped = paid + timedelta(days=random.randint(1, 3)) if status in ('shipped', 'delivered', 'completed') else None
        delivered = shipped + timedelta(days=random.randint(1, 5)) if status in ('delivered', 'completed') else None
        completed = delivered + timedelta(hours=random.randint(1, 48)) if status == 'completed' else None
        cancel_r = random.choice(RETURN_REASONS) if status == 'cancelled' else None
        coupon = random.choice([None, None, None, 'SAVE10', 'NEW20', 'VIP15'])

        r = await conn.execute(
            text("INSERT INTO t_orders (user_id, order_no, total_amount, discount_amount, shipping_fee, "
                 "coupon_code, status, city, province, note, cancel_reason, "
                 "created_at, paid_at, shipped_at, delivered_at, completed_at) "
                 "VALUES (:uid, :ono, :ta, :da, :sf, :cc, :st, :c, :pr, :n, :cr, :ca, :pa, :sa, :dla, :co)"),
            {'uid': uid, 'ono': order_no, 'ta': total, 'da': discount, 'sf': shipping,
             'cc': coupon, 'st': status, 'c': city, 'pr': province, 'n': note, 'cr': cancel_r,
             'ca': created, 'pa': paid, 'sa': shipped, 'dla': delivered, 'co': completed})
        order_ids.append(next_id(r))
        order_user_ids.append(uid)

    if not is_mysql:
        r = await conn.execute(text("SELECT id FROM t_orders ORDER BY id"))
        order_ids = [row[0] for row in r.fetchall()]

    # --- Order Items ---
    pr = await conn.execute(text("SELECT id, name, price FROM t_products"))
    products = [(row[0], row[1], float(row[2])) for row in pr.fetchall()]
    item_count = 0
    for oid in order_ids:
        n_items = random.choices([1, 2, 3, 4, 5], weights=[40, 30, 15, 10, 5])[0]
        picked = random.sample(products, min(n_items, len(products)))
        for pid, pname, price in picked:
            qty = random.randint(1, 3)
            await conn.execute(
                text("INSERT INTO t_order_items (order_id, product_id, product_name, quantity, price, subtotal) "
                     "VALUES (:oid, :pid, :pn, :q, :p, :sub)"),
                {'oid': oid, 'pid': pid, 'pn': pname, 'q': qty, 'p': price, 'sub': round(price * qty, 2)})
            item_count += 1

    # --- Payments ---
    for oid in order_ids[:1600]:
        method = random.choice(PAYMENT_METHODS)
        txn_id = f'TXN{random.randint(10000000000, 99999999999)}'
        p_status = random.choices(['success', 'failed', 'pending', 'refunded'], weights=[88, 4, 3, 5])[0]
        refund = round(random.uniform(10, 500), 2) if p_status == 'refunded' else 0
        await conn.execute(
            text("INSERT INTO t_payments (order_id, amount, method, transaction_id, status, refund_amount, paid_at) "
                 "VALUES (:oid, :amt, :m, :txn, :s, :ra, :pa)"),
            {'oid': oid, 'amt': round(random.uniform(29.9, 25000), 2), 'm': method,
             'txn': txn_id, 's': p_status, 'ra': refund,
             'pa': base_time + timedelta(days=random.randint(0, 179))})

    # --- Shipping ---
    for oid in order_ids[:1200]:
        carrier = random.choice(CARRIERS)
        tracking = f'{random.randint(100000000000, 999999999999)}'
        shipped = base_time + timedelta(days=random.randint(1, 170))
        delivered = shipped + timedelta(days=random.randint(1, 7)) if random.random() > 0.15 else None
        status = 'delivered' if delivered else 'in_transit'
        await conn.execute(
            text("INSERT INTO t_shipping (order_id, carrier, tracking_no, shipped_at, delivered_at, status) "
                 "VALUES (:oid, :c, :t, :sa, :da, :s)"),
            {'oid': oid, 'c': carrier, 't': tracking, 'sa': shipped, 'da': delivered, 's': status})

    # --- Reviews ---
    review_contents = ['质量很好，非常满意', '物流很快', '性价比很高', '物超所值', '一般般', '价格便宜', '推荐购买',
                       '第二次购买了', '颜色和图片有差异', '尺寸不合适', '超值！', '客服态度很好', '物流太慢了',
                       '比实体店便宜很多', '会回购的', '发货速度超快']
    review_count = 0
    for i in random.sample(order_ids[:1200], min(800, 1200)):
        rating = random.choices([1, 2, 3, 4, 5], weights=[2, 3, 8, 25, 62])[0]
        images = random.choices([0, 1, 2, 3], weights=[30, 25, 20, 25])[0]
        uid = random.choice(user_ids)
        pr = await conn.execute(text("SELECT product_id FROM t_order_items WHERE order_id = :oid LIMIT 1"), {'oid': i})
        prow = pr.fetchone()
        pid = prow[0] if prow else product_ids[0]
        await conn.execute(
            text("INSERT INTO t_reviews (order_id, product_id, user_id, rating, content, images, "
                 "is_anonymous, helpful_count, created_at) "
                 "VALUES (:oid, :pid, :uid, :r, :c, :img, 0, 0, :ca)"),
            {'oid': i, 'pid': pid, 'uid': uid, 'r': rating, 'c': random.choice(review_contents),
             'img': images, 'ca': base_time + timedelta(days=random.randint(1, 179))})
        review_count += 1

    # --- Returns ---
    for i in random.sample(order_ids[:500], min(150, 500)):
        reason = random.choice(RETURN_REASONS)
        refund = round(random.uniform(29, 3000), 2)
        status = random.choices(['pending', 'approved', 'shipped', 'completed', 'rejected'], weights=[20, 30, 15, 25, 10])[0]
        uid = random.choice(user_ids[:100])
        await conn.execute(
            text("INSERT INTO t_returns (order_id, user_id, reason, refund_amount, status, "
                 "carrier, tracking_no, created_at, approved_at, completed_at) "
                 "VALUES (:oid, :uid, :r, :ra, :s, :c, :t, :ca, :aa, :coa)"),
            {'oid': i, 'uid': uid, 'r': reason, 'ra': refund, 's': status,
             'c': random.choice(CARRIERS) if status in ('shipped', 'completed') else None,
             't': f'{random.randint(100000000000, 999999999999)}' if status in ('shipped', 'completed') else None,
             'ca': base_time + timedelta(days=random.randint(10, 179)),
             'aa': base_time + timedelta(days=random.randint(12, 179)) if status != 'pending' else None,
             'coa': base_time + timedelta(days=random.randint(15, 179)) if status == 'completed' else None})

    # --- Coupons ---
    coupon_data = [
        ('SAVE10', '满100减10', 'fixed', 10, 100, 500),
        ('NEW20', '新用户立减20', 'fixed', 20, 0, 1000),
        ('VIP15', 'VIP专属95折', 'percent', 15, 50, 200),
        ('SUMMER5', '夏季促销95折', 'percent', 5, 200, 2000),
        ('BIG50', '满300减50', 'fixed', 50, 300, 300),
        ('MEGA100', '满500减100', 'fixed', 100, 500, 100),
        ('FLASH30', '闪购30%OFF', 'percent', 30, 200, 50),
        ('WELCOME88', '新人礼包88折', 'percent', 12, 0, 500),
    ]
    for code, name, dtype, value, min_ord, max_u in coupon_data:
        await conn.execute(
            text("INSERT INTO t_coupons (code, name, discount_type, discount_value, min_order, "
                 "max_uses, per_user_limit, valid_from, valid_until, is_active) "
                 "VALUES (:c, :n, :dt, :dv, :mo, :mu, :pul, :vf, :vu, 1)"),
            {'c': code, 'n': name, 'dt': dtype, 'dv': value, 'mo': min_ord,
             'mu': max_u, 'pul': 1,
             'vf': base_time, 'vu': base_time + timedelta(days=365)})

    if not is_mysql:
        r = await conn.execute(text("SELECT id FROM t_coupons ORDER BY id"))
        coupon_ids = [row[0] for row in r.fetchall()]
    else:
        coupon_ids = list(range(1, len(coupon_data) + 1))

    for uid in user_ids[:200]:
        picked = random.sample(coupon_ids, random.randint(1, 5))
        for cid in picked:
            status = random.choices(['unused', 'used', 'expired'], weights=[50, 35, 15])[0]
            await conn.execute(
                text("INSERT INTO t_user_coupons (user_id, coupon_id, status, used_at, created_at) "
                     "VALUES (:uid, :cid, :s, :ua, :ca)"),
                {'uid': uid, 'cid': cid, 's': status,
                 'ua': base_time + timedelta(days=random.randint(30, 150)) if status == 'used' else None,
                 'ca': base_time + timedelta(days=random.randint(0, 100))})

    # --- Warehouses ---
    wh_data = [
        ('华东仓', '上海', '浙江', 50000, '上海市松江区XX路999号', '王经理'),
        ('华南仓', '广州', '广东', 40000, '广州市白云区YY路888号', '李经理'),
        ('华北仓', '北京', '北京', 35000, '北京市大兴区ZZ路777号', '赵经理'),
        ('西南仓', '成都', '四川', 25000, '成都市双流区AA路666号', '刘经理'),
        ('华中仓', '武汉', '湖北', 20000, '武汉市东西湖区BB路555号', '陈经理'),
        ('东北仓', '大连', '辽宁', 15000, '大连市金州区CC路444号', '杨经理'),
    ]
    wh_ids = []
    for name, city, province, cap, addr, mgr in wh_data:
        r = await conn.execute(
            text("INSERT INTO t_warehouses (name, city, province, capacity, address, manager_name, manager_phone, is_active) "
                 "VALUES (:n, :c, :pr, :cap, :addr, :mgr, :mp, 1)"),
            {'n': name, 'c': city, 'pr': province, 'cap': cap, 'addr': addr, 'mgr': mgr,
             'mp': f'1{random.randint(3,9)}{random.randint(100000000,999999999)}'})
        wh_ids.append(next_id(r))
    if not is_mysql:
        r = await conn.execute(text("SELECT id FROM t_warehouses ORDER BY id"))
        wh_ids = [row[0] for row in r.fetchall()]

    # --- Inventory ---
    inv_count = 0
    for pid in product_ids:
        for wid in random.sample(wh_ids, random.randint(1, 4)):
            qty = random.randint(10, 800)
            reserved = random.randint(0, qty // 4)
            await conn.execute(
                text("INSERT INTO t_inventory (product_id, warehouse_id, quantity, reserved, min_stock) "
                     "VALUES (:pid, :wid, :q, :r, :ms)"),
                {'pid': pid, 'wid': wid, 'q': qty, 'r': reserved, 'ms': 20})
            inv_count += 1

    return {
        'users': len(user_ids), 'products': len(product_ids), 'orders': len(order_ids),
        'items': item_count, 'reviews': review_count, 'inventory': inv_count,
        'warehouses': len(wh_ids),
    }


async def init_mysql(base_time):
    """Initialize MySQL test database."""
    print(f'\n===== MySQL: {MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB} =====')
    root_url = f'mysql+aiomysql://{MYSQL_USER}:{MYSQL_PASS}@{MYSQL_HOST}:{MYSQL_PORT}'
    eng = create_async_engine(root_url)
    async with eng.begin() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS {MYSQL_DB}'))
        await conn.execute(text(f'CREATE DATABASE {MYSQL_DB} DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'))
    await eng.dispose()

    db_url = f'mysql+aiomysql://{MYSQL_USER}:{MYSQL_PASS}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}'
    db = create_async_engine(db_url)
    async with db.begin() as conn:
        for _name, ddl in TABLE_DDL_MYSQL:
            await conn.execute(text(ddl))

    async with db.begin() as conn:
        stats = await seed_database(conn, is_mysql=True, base_time=base_time)
    await db.dispose()

    print(f'MySQL seed complete:')
    for k, v in stats.items():
        print(f'  {k}: {v}')


async def init_pg(base_time):
    """Initialize PostgreSQL test database."""
    print(f'\n===== PostgreSQL: {PG_HOST}:{PG_PORT}/{PG_DB} =====')
    root_url = f'postgresql+asyncpg://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/postgres'

    # DROP/CREATE DATABASE must run outside a transaction (AUTOCOMMIT)
    eng = create_async_engine(root_url)
    try:
        async with eng.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{PG_DB}' AND pid != pg_backend_pid()"))
            await conn.execute(text(f'DROP DATABASE IF EXISTS {PG_DB}'))
            await conn.execute(text(f'CREATE DATABASE {PG_DB}'))
        print(f'  Database {PG_DB} created successfully')
    except Exception as e:
        print(f'Warning: Could not create database: {e}')
    finally:
        await eng.dispose()

    db_url = f'postgresql+asyncpg://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{PG_DB}'
    db = create_async_engine(db_url)

    # DDL - create all tables
    async with db.begin() as conn:
        for _name, ddl in TABLE_DDL_PG:
            await conn.execute(text(ddl))

        # PG needs indexes explicitly
        await conn.execute(text('CREATE INDEX idx_users_city ON t_users(city)'))
        await conn.execute(text('CREATE INDEX idx_users_vip ON t_users(vip_level)'))
        await conn.execute(text('CREATE INDEX idx_users_created ON t_users(created_at)'))
        await conn.execute(text('CREATE INDEX idx_addresses_user ON t_addresses(user_id)'))
        await conn.execute(text('CREATE INDEX idx_categories_parent ON t_categories(parent_id)'))
        await conn.execute(text('CREATE INDEX idx_products_category ON t_products(category_id)'))
        await conn.execute(text('CREATE INDEX idx_products_brand ON t_products(brand_id)'))
        await conn.execute(text('CREATE INDEX idx_products_status ON t_products(status)'))
        await conn.execute(text('CREATE INDEX idx_orders_user ON t_orders(user_id)'))
        await conn.execute(text('CREATE INDEX idx_orders_status ON t_orders(status)'))
        await conn.execute(text('CREATE INDEX idx_orders_created ON t_orders(created_at)'))
        await conn.execute(text('CREATE INDEX idx_order_items_order ON t_order_items(order_id)'))
        await conn.execute(text('CREATE INDEX idx_order_items_product ON t_order_items(product_id)'))
        await conn.execute(text('CREATE INDEX idx_payments_order ON t_payments(order_id)'))
        await conn.execute(text('CREATE INDEX idx_payments_method ON t_payments(method)'))
        await conn.execute(text('CREATE INDEX idx_shipping_order ON t_shipping(order_id)'))
        await conn.execute(text('CREATE INDEX idx_reviews_order ON t_reviews(order_id)'))
        await conn.execute(text('CREATE INDEX idx_reviews_product ON t_reviews(product_id)'))
        await conn.execute(text('CREATE INDEX idx_reviews_rating ON t_reviews(rating)'))
        await conn.execute(text('CREATE INDEX idx_returns_order ON t_returns(order_id)'))
        await conn.execute(text('CREATE INDEX idx_returns_status ON t_returns(status)'))
        await conn.execute(text('CREATE INDEX idx_user_coupons_user ON t_user_coupons(user_id)'))

    # Seed data
    async with db.begin() as conn:
        stats = await seed_database(conn, is_mysql=False, base_time=base_time)
    await db.dispose()

    print(f'PostgreSQL seed complete:')
    for k, v in stats.items():
        print(f'  {k}: {v}')


async def main():
    parser = argparse.ArgumentParser(description='Initialize test databases for ChatBI')
    parser.add_argument('--mysql-only', action='store_true', help='Only initialize MySQL')
    parser.add_argument('--pg-only', action='store_true', help='Only initialize PostgreSQL')
    args = parser.parse_args()

    random.seed(42)
    base_time = datetime.now() - timedelta(days=180)

    if not args.pg_only:
        await init_mysql(base_time)

    if not args.mysql_only:
        await init_pg(base_time)

    print('\nDone! Test databases initialized.')


if __name__ == '__main__':
    asyncio.run(main())
