"""
ChatBI v2 — 电商演示数据库生成脚本

生成 ~120 张表 + 业务样本数据的 DDL+DML SQL，用于:
  1. 在 PG 建库建表 (chatbi_ecom)
  2. 插入样本数据
  3. 通过 ChatBI API 接入数据源 → 扫描 → 生成语义层

用法:
  python backend/scripts/gen_ecom_demo.py > backend/scripts/ecom_demo.sql
  psql -h localhost -U root -d chatbi_ecom -f backend/scripts/ecom_demo.sql
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta

# ── 可配置参数 ──────────────────────────────────────────────
SEED = 42
N_USERS = 500
N_PRODUCTS = 300
N_SHOPS = 30
N_ORDERS = 2000
N_COUPONS = 50
N_PROMOTIONS = 20

random.seed(SEED)

# ── 中文 faker 辅助 ─────────────────────────────────────────
SURNAMES = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐")
GIVEN_NAMES = list("伟芳娜敏静丽强磊洋勇艳杰娟涛明超秀霞平刚桂英华慧巧美惠珍贞莉兰凤洁梅琳素云莲真环雪荣爱妹霖琴")
CATEGORIES_L1 = ["女装", "男装", "手机数码", "电脑办公", "家用电器", "美妆护肤", "个护清洁", "食品饮料", "母婴", "运动户外", "鞋靴箱包", "家居家装", "图书文具", "珠宝配饰"]
CATEGORIES_L2 = {
    "女装": ["连衣裙", "T恤", "衬衫", "半身裙", "牛仔裤", "风衣", "羽绒服"],
    "男装": ["T恤", "衬衫", "西裤", "牛仔裤", "夹克", "POLO衫", "卫衣"],
    "手机数码": ["手机", "平板", "耳机", "智能手表", "充电器", "数据线"],
    "电脑办公": ["笔记本", "台式机", "显示器", "键盘鼠标", "打印机", "办公椅"],
    "家用电器": ["空调", "冰箱", "洗衣机", "电视", "微波炉", "吸尘器"],
    "美妆护肤": ["面霜", "精华", "防晒", "口红", "粉底", "面膜"],
    "个护清洁": ["洗发水", "沐浴露", "牙膏", "洗衣液", "纸巾", "洗手液"],
    "食品饮料": ["零食", "牛奶", "茶叶", "咖啡", "坚果", "饮料"],
    "母婴": ["奶粉", "纸尿裤", "婴儿车", "玩具", "童装", "辅食"],
    "运动户外": ["跑步鞋", "瑜伽服", "帐篷", "羽毛球拍", "篮球", "冲锋衣"],
    "鞋靴箱包": ["女鞋", "男鞋", "运动鞋", "双肩包", "手提包", "行李箱"],
    "家居家装": ["床品", "沙发", "灯具", "窗帘", "收纳", "厨具"],
    "图书文具": ["小说", "教材", "笔记本", "钢笔", "画册", "益智玩具"],
    "珠宝配饰": ["项链", "手链", "耳环", "戒指", "手表", "太阳镜"],
}
BRANDS = [
    "优衣库", "ZARA", "Nike", "Adidas", "Apple", "Samsung", "Huawei", "Xiaomi",
    "兰蔻", "雅诗兰黛", "欧莱雅", "SK-II", "资生堂", "海蓝之谜",
    "美的", "格力", "海尔", "索尼", "松下", "飞利浦",
    "三只松鼠", "良品铺子", "百草味", "蒙牛", "伊利", "农夫山泉",
    "李宁", "安踏", "特步", "FILA", "波司登", "海澜之家",
]
WAREHOUSE_CITIES = ["北京", "上海", "广州", "深圳", "成都", "武汉", "杭州", "南京", "重庆", "西安"]
LOGISTICS_COMPANIES = ["顺丰速运", "中通快递", "圆通速递", "韵达快递", "申通快递", "百世快递", "极兔速递"]
PAYMENT_METHODS = ["支付宝", "微信支付", "银行卡", "花呗", "信用卡"]
ORDER_STATUS = ["pending", "paid", "shipped", "delivered", "cancelled", "refunded"]
PROVINCES = ["北京", "上海", "广东", "浙江", "江苏", "四川", "湖北", "福建", "山东", "河南"]
ROLES = ["admin", "operator", "viewer"]
CS_STATUS = ["pending", "processing", "resolved", "closed"]
AFTER_SALES_TYPE = ["refund_only", "return_refund", "exchange"]
TICKET_SOURCE = ["web", "app", "phone", "wechat"]


def gen_name():
    return random.choice(SURNAMES) + random.choice(GIVEN_NAMES) + (random.choice(GIVEN_NAMES) if random.random() > 0.5 else "")


def gen_phone():
    return "1" + random.choice(["3", "5", "7", "8", "9"]) + "".join(str(random.randint(0, 9)) for _ in range(9))


def gen_email(name):
    domains = ["qq.com", "163.com", "gmail.com", "outlook.com", "foxmail.com"]
    return f"{name}_{random.randint(100, 999)}@{random.choice(domains)}"


def gen_ip():
    return f"{random.randint(1, 255)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 255)}"


def gen_date(start_days=365, end_days=0):
    d = datetime.now() - timedelta(days=random.randint(end_days, start_days))
    return d.strftime("%Y-%m-%d")


def gen_datetime(start_days=365, end_days=0):
    d = datetime.now() - timedelta(days=random.randint(end_days, start_days), hours=random.randint(0, 23), minutes=random.randint(0, 59))
    return d.strftime("%Y-%m-%d %H:%M:%S")


def gen_price(lo=1, hi=9999):
    return round(random.uniform(lo, hi), 2)


def esc(s: str) -> str:
    """SQL 转义单引号"""
    return s.replace("'", "''")


# ── DDL 生成 ────────────────────────────────────────────────

def ddl():
    lines = [
        "-- ChatBI 电商演示数据库 DDL+DML",
        "-- 生成时间: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "",
    ]

    # ═══ 用户中心 (uc_) ═══
    lines += [
        "-- ═══════════════════════════════════════════════",
        "-- 用户中心 (uc_)",
        "-- ═══════════════════════════════════════════════",
        "",
        """CREATE TABLE IF NOT EXISTS uc_users (
  id BIGSERIAL PRIMARY KEY,
  username VARCHAR(64) NOT NULL,
  nickname VARCHAR(64),
  phone VARCHAR(20),
  email VARCHAR(128),
  gender SMALLINT DEFAULT 0,
  avatar_url VARCHAR(512),
  birthday DATE,
  status SMALLINT NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE uc_users IS '用户表';
COMMENT ON COLUMN uc_users.id IS '用户ID';
COMMENT ON COLUMN uc_users.username IS '用户名';
COMMENT ON COLUMN uc_users.nickname IS '昵称';
COMMENT ON COLUMN uc_users.phone IS '手机号';
COMMENT ON COLUMN uc_users.email IS '邮箱';
COMMENT ON COLUMN uc_users.gender IS '性别(0未知1男2女)';
COMMENT ON COLUMN uc_users.avatar_url IS '头像URL';
COMMENT ON COLUMN uc_users.birthday IS '生日';
COMMENT ON COLUMN uc_users.status IS '状态(0禁用1正常)';
COMMENT ON COLUMN uc_users.created_at IS '注册时间';
COMMENT ON COLUMN uc_users.updated_at IS '更新时间';
""",
        """CREATE TABLE IF NOT EXISTS uc_user_profiles (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  real_name VARCHAR(64),
  id_card VARCHAR(18),
  province VARCHAR(32),
  city VARCHAR(32),
  district VARCHAR(32),
  bio TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE uc_user_profiles IS '用户实名信息';
COMMENT ON COLUMN uc_user_profiles.real_name IS '真实姓名';
COMMENT ON COLUMN uc_user_profiles.id_card IS '身份证号';
COMMENT ON COLUMN uc_user_profiles.province IS '省份';
COMMENT ON COLUMN uc_user_profiles.city IS '城市';
COMMENT ON COLUMN uc_user_profiles.district IS '区县';
COMMENT ON COLUMN uc_user_profiles.bio IS '个人简介';
""",
        """CREATE TABLE IF NOT EXISTS uc_addresses (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  receiver_name VARCHAR(64) NOT NULL,
  phone VARCHAR(20) NOT NULL,
  province VARCHAR(32) NOT NULL,
  city VARCHAR(32) NOT NULL,
  district VARCHAR(32) NOT NULL,
  detail_address VARCHAR(256) NOT NULL,
  is_default BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE uc_addresses IS '收货地址';
COMMENT ON COLUMN uc_addresses.receiver_name IS '收件人';
COMMENT ON COLUMN uc_addresses.phone IS '收件人电话';
COMMENT ON COLUMN uc_addresses.province IS '省份';
COMMENT ON COLUMN uc_addresses.city IS '城市';
COMMENT ON COLUMN uc_addresses.district IS '区县';
COMMENT ON COLUMN uc_addresses.detail_address IS '详细地址';
COMMENT ON COLUMN uc_addresses.is_default IS '是否默认';
""",
        """CREATE TABLE IF NOT EXISTS uc_memberships (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  level SMALLINT NOT NULL DEFAULT 1,
  points INT NOT NULL DEFAULT 0,
  total_spent DECIMAL(12,2) NOT NULL DEFAULT 0,
  expire_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE uc_memberships IS '会员等级';
COMMENT ON COLUMN uc_memberships.level IS '等级(1-6)';
COMMENT ON COLUMN uc_memberships.points IS '积分余额';
COMMENT ON COLUMN uc_memberships.total_spent IS '累计消费';
COMMENT ON COLUMN uc_memberships.expire_at IS '到期时间';
""",
        """CREATE TABLE IF NOT EXISTS uc_points_accounts (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  balance INT NOT NULL DEFAULT 0,
  total_earned INT NOT NULL DEFAULT 0,
  total_spent INT NOT NULL DEFAULT 0,
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE uc_points_accounts IS '积分账户';
COMMENT ON COLUMN uc_points_accounts.balance IS '积分余额';
COMMENT ON COLUMN uc_points_accounts.total_earned IS '累计获得';
COMMENT ON COLUMN uc_points_accounts.total_spent IS '累计消耗';
""",
        """CREATE TABLE IF NOT EXISTS uc_points_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  change_amount INT NOT NULL,
  balance_after INT NOT NULL,
  source_type VARCHAR(32) NOT NULL,
  source_id BIGINT,
  remark VARCHAR(256),
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE uc_points_logs IS '积分流水';
COMMENT ON COLUMN uc_points_logs.change_amount IS '变动数量(正得负消)';
COMMENT ON COLUMN uc_points_logs.balance_after IS '变动后余额';
COMMENT ON COLUMN uc_points_logs.source_type IS '来源类型(order/sign_in/activity)';
COMMENT ON COLUMN uc_points_logs.source_id IS '来源ID';
COMMENT ON COLUMN uc_points_logs.remark IS '备注';
""",
        """CREATE TABLE IF NOT EXISTS uc_user_tags (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  tag_name VARCHAR(64) NOT NULL,
  tag_value VARCHAR(128),
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE uc_user_tags IS '用户标签';
COMMENT ON COLUMN uc_user_tags.tag_name IS '标签名';
COMMENT ON COLUMN uc_user_tags.tag_value IS '标签值';
""",
        """CREATE TABLE IF NOT EXISTS uc_login_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  login_ip VARCHAR(64),
  login_device VARCHAR(64),
  login_method VARCHAR(32),
  location VARCHAR(128),
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE uc_login_logs IS '登录日志';
COMMENT ON COLUMN uc_login_logs.login_ip IS '登录IP';
COMMENT ON COLUMN uc_login_logs.login_device IS '登录设备';
COMMENT ON COLUMN uc_login_logs.login_method IS '登录方式(password/sms/oauth)';
COMMENT ON COLUMN uc_login_logs.location IS '登录地点';
""",
        """CREATE TABLE IF NOT EXISTS uc_oauth_accounts (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  provider VARCHAR(32) NOT NULL,
  openid VARCHAR(256) NOT NULL,
  union_id VARCHAR(256),
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE uc_oauth_accounts IS '第三方账号绑定';
COMMENT ON COLUMN uc_oauth_accounts.provider IS '平台(wechat/alipay/weibo)';
COMMENT ON COLUMN uc_oauth_accounts.openid IS 'OpenID';
COMMENT ON COLUMN uc_oauth_accounts.union_id IS 'UnionID';
""",
        """CREATE TABLE IF NOT EXISTS uc_user_preferences (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  pref_key VARCHAR(64) NOT NULL,
  pref_value TEXT,
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE uc_user_preferences IS '用户偏好设置';
COMMENT ON COLUMN uc_user_preferences.pref_key IS '偏好键';
COMMENT ON COLUMN uc_user_preferences.pref_value IS '偏好值';
""",
        """CREATE TABLE IF NOT EXISTS uc_feedbacks (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  type VARCHAR(32) NOT NULL,
  content TEXT NOT NULL,
  contact VARCHAR(128),
  status SMALLINT DEFAULT 0,
  reply TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE uc_feedbacks IS '用户反馈';
COMMENT ON COLUMN uc_feedbacks.type IS '类型(bug/suggestion/complaint)';
COMMENT ON COLUMN uc_feedbacks.content IS '反馈内容';
COMMENT ON COLUMN uc_feedbacks.contact IS '联系方式';
COMMENT ON COLUMN uc_feedbacks.status IS '状态(0待处理1已回复2已关闭)';
""",
        """CREATE TABLE IF NOT EXISTS uc_blacklists (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  reason VARCHAR(256) NOT NULL,
  blocked_by VARCHAR(64),
  blocked_at TIMESTAMP NOT NULL DEFAULT NOW(),
  expire_at TIMESTAMP
);
COMMENT ON TABLE uc_blacklists IS '黑名单';
COMMENT ON COLUMN uc_blacklists.reason IS '封禁原因';
COMMENT ON COLUMN uc_blacklists.blocked_by IS '操作人';
COMMENT ON COLUMN uc_blacklists.expire_at IS '解封时间';
""",
    ]

    # ═══ 商品中心 (pd_) ═══
    lines += [
        "-- ═══════════════════════════════════════════════",
        "-- 商品中心 (pd_)",
        "-- ═══════════════════════════════════════════════",
        "",
        """CREATE TABLE IF NOT EXISTS pd_categories (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(64) NOT NULL,
  parent_id BIGINT REFERENCES pd_categories(id),
  level SMALLINT NOT NULL DEFAULT 1,
  sort_order INT DEFAULT 0,
  icon_url VARCHAR(512),
  is_visible BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE pd_categories IS '商品分类';
COMMENT ON COLUMN pd_categories.name IS '分类名称';
COMMENT ON COLUMN pd_categories.parent_id IS '父分类ID';
COMMENT ON COLUMN pd_categories.level IS '层级(1/2/3)';
COMMENT ON COLUMN pd_categories.sort_order IS '排序';
COMMENT ON COLUMN pd_categories.icon_url IS '图标URL';
COMMENT ON COLUMN pd_categories.is_visible IS '是否可见';
""",
        """CREATE TABLE IF NOT EXISTS pd_category_attrs (
  id BIGSERIAL PRIMARY KEY,
  category_id BIGINT NOT NULL REFERENCES pd_categories(id),
  attr_name VARCHAR(64) NOT NULL,
  attr_values TEXT,
  is_required BOOLEAN DEFAULT FALSE,
  sort_order INT DEFAULT 0
);
COMMENT ON TABLE pd_category_attrs IS '分类属性';
COMMENT ON COLUMN pd_category_attrs.attr_name IS '属性名';
COMMENT ON COLUMN pd_category_attrs.attr_values IS '可选值(JSON数组)';
COMMENT ON COLUMN pd_category_attrs.is_required IS '是否必填';
""",
        """CREATE TABLE IF NOT EXISTS pd_brands (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(64) NOT NULL,
  logo_url VARCHAR(512),
  description TEXT,
  country VARCHAR(64),
  is_verified BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE pd_brands IS '品牌表';
COMMENT ON COLUMN pd_brands.name IS '品牌名';
COMMENT ON COLUMN pd_brands.logo_url IS 'Logo URL';
COMMENT ON COLUMN pd_brands.description IS '品牌描述';
COMMENT ON COLUMN pd_brands.country IS '所属国家';
COMMENT ON COLUMN pd_brands.is_verified IS '是否认证';
""",
        """CREATE TABLE IF NOT EXISTS pd_products (
  id BIGSERIAL PRIMARY KEY,
  shop_id BIGINT,
  category_id BIGINT REFERENCES pd_categories(id),
  brand_id BIGINT REFERENCES pd_brands(id),
  name VARCHAR(256) NOT NULL,
  subtitle VARCHAR(512),
  main_image VARCHAR(512),
  description TEXT,
  price DECIMAL(12,2) NOT NULL,
  original_price DECIMAL(12,2),
  status SMALLINT NOT NULL DEFAULT 1,
  is_on_sale BOOLEAN DEFAULT TRUE,
  sales_count INT DEFAULT 0,
  view_count INT DEFAULT 0,
  avg_rating DECIMAL(3,2) DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE pd_products IS '商品SPU表';
COMMENT ON COLUMN pd_products.shop_id IS '店铺ID';
COMMENT ON COLUMN pd_products.category_id IS '分类ID';
COMMENT ON COLUMN pd_products.brand_id IS '品牌ID';
COMMENT ON COLUMN pd_products.name IS '商品名称';
COMMENT ON COLUMN pd_products.subtitle IS '副标题';
COMMENT ON COLUMN pd_products.main_image IS '主图URL';
COMMENT ON COLUMN pd_products.description IS '商品详情';
COMMENT ON COLUMN pd_products.price IS '销售价';
COMMENT ON COLUMN pd_products.original_price IS '原价';
COMMENT ON COLUMN pd_products.status IS '状态(0下架1上架2审核)';
COMMENT ON COLUMN pd_products.is_on_sale IS '是否促销';
COMMENT ON COLUMN pd_products.sales_count IS '销量';
COMMENT ON COLUMN pd_products.view_count IS '浏览量';
COMMENT ON COLUMN pd_products.avg_rating IS '平均评分';
""",
        """CREATE TABLE IF NOT EXISTS pd_product_skus (
  id BIGSERIAL PRIMARY KEY,
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  sku_code VARCHAR(64) NOT NULL,
  spec_values JSONB,
  price DECIMAL(12,2) NOT NULL,
  stock INT NOT NULL DEFAULT 0,
  safety_stock INT DEFAULT 10,
  barcode VARCHAR(64),
  weight DECIMAL(8,2),
  status SMALLINT DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE pd_product_skus IS '商品SKU表';
COMMENT ON COLUMN pd_product_skus.sku_code IS 'SKU编码';
COMMENT ON COLUMN pd_product_skus.spec_values IS '规格值(如{"颜色":"红","尺码":"L"})';
COMMENT ON COLUMN pd_product_skus.price IS 'SKU价格';
COMMENT ON COLUMN pd_product_skus.stock IS '库存';
COMMENT ON COLUMN pd_product_skus.safety_stock IS '安全库存';
COMMENT ON COLUMN pd_product_skus.barcode IS '条形码';
COMMENT ON COLUMN pd_product_skus.weight IS '重量(kg)';
""",
        """CREATE TABLE IF NOT EXISTS pd_product_images (
  id BIGSERIAL PRIMARY KEY,
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  image_url VARCHAR(512) NOT NULL,
  sort_order INT DEFAULT 0
);
COMMENT ON TABLE pd_product_images IS '商品图片';
COMMENT ON COLUMN pd_product_images.image_url IS '图片URL';
COMMENT ON COLUMN pd_product_images.sort_order IS '排序';
""",
        """CREATE TABLE IF NOT EXISTS pd_product_attrs (
  id BIGSERIAL PRIMARY KEY,
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  attr_name VARCHAR(64) NOT NULL,
  attr_value VARCHAR(256) NOT NULL
);
COMMENT ON TABLE pd_product_attrs IS '商品属性值';
COMMENT ON COLUMN pd_product_attrs.attr_name IS '属性名(如材质/产地)';
COMMENT ON COLUMN pd_product_attrs.attr_value IS '属性值';
""",
        """CREATE TABLE IF NOT EXISTS pd_product_tags (
  id BIGSERIAL PRIMARY KEY,
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  tag_name VARCHAR(64) NOT NULL
);
COMMENT ON TABLE pd_product_tags IS '商品标签';
COMMENT ON COLUMN pd_product_tags.tag_name IS '标签名(如新品/热销/限量)';
""",
        """CREATE TABLE IF NOT EXISTS pd_sku_prices (
  id BIGSERIAL PRIMARY KEY,
  sku_id BIGINT NOT NULL REFERENCES pd_product_skus(id),
  price DECIMAL(12,2) NOT NULL,
  start_at TIMESTAMP,
  end_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE pd_sku_prices IS 'SKU价格历史';
COMMENT ON COLUMN pd_sku_prices.price IS '价格';
COMMENT ON COLUMN pd_sku_prices.start_at IS '生效开始';
COMMENT ON COLUMN pd_sku_prices.end_at IS '生效结束';
""",
        """CREATE TABLE IF NOT EXISTS pd_sku_stocks (
  id BIGSERIAL PRIMARY KEY,
  sku_id BIGINT NOT NULL REFERENCES pd_product_skus(id),
  warehouse_id BIGINT,
  available_stock INT NOT NULL DEFAULT 0,
  locked_stock INT NOT NULL DEFAULT 0,
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE pd_sku_stocks IS 'SKU分仓库存';
COMMENT ON COLUMN pd_sku_stocks.warehouse_id IS '仓库ID';
COMMENT ON COLUMN pd_sku_stocks.available_stock IS '可用库存';
COMMENT ON COLUMN pd_sku_stocks.locked_stock IS '锁定库存';
""",
        """CREATE TABLE IF NOT EXISTS pd_price_histories (
  id BIGSERIAL PRIMARY KEY,
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  old_price DECIMAL(12,2),
  new_price DECIMAL(12,2) NOT NULL,
  changed_by VARCHAR(64),
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE pd_price_histories IS '价格变更记录';
COMMENT ON COLUMN pd_price_histories.old_price IS '原价';
COMMENT ON COLUMN pd_price_histories.new_price IS '新价';
COMMENT ON COLUMN pd_price_histories.changed_by IS '操作人';
""",
        """CREATE TABLE IF NOT EXISTS pd_product_reviews (
  id BIGSERIAL PRIMARY KEY,
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  order_item_id BIGINT,
  rating SMALLINT NOT NULL,
  content TEXT,
  images JSONB,
  is_anonymous BOOLEAN DEFAULT FALSE,
  like_count INT DEFAULT 0,
  status SMALLINT DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE pd_product_reviews IS '商品评价';
COMMENT ON COLUMN pd_product_reviews.rating IS '评分(1-5)';
COMMENT ON COLUMN pd_product_reviews.content IS '评价内容';
COMMENT ON COLUMN pd_product_reviews.images IS '评价图片(JSON数组)';
COMMENT ON COLUMN pd_product_reviews.is_anonymous IS '是否匿名';
COMMENT ON COLUMN pd_product_reviews.like_count IS '点赞数';
COMMENT ON COLUMN pd_product_reviews.status IS '状态(0隐藏1正常)';
""",
        """CREATE TABLE IF NOT EXISTS pd_review_images (
  id BIGSERIAL PRIMARY KEY,
  review_id BIGINT NOT NULL REFERENCES pd_product_reviews(id),
  image_url VARCHAR(512) NOT NULL,
  sort_order INT DEFAULT 0
);
COMMENT ON TABLE pd_review_images IS '评价图片';
""",
        """CREATE TABLE IF NOT EXISTS pd_product_qa (
  id BIGSERIAL PRIMARY KEY,
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  question TEXT NOT NULL,
  answer TEXT,
  answered_by BIGINT,
  like_count INT DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE pd_product_qa IS '商品问答';
COMMENT ON COLUMN pd_product_qa.question IS '提问内容';
COMMENT ON COLUMN pd_product_qa.answer IS '回答内容';
COMMENT ON COLUMN pd_product_qa.like_count IS '点赞数';
""",
        """CREATE TABLE IF NOT EXISTS pd_spu_specs (
  id BIGSERIAL PRIMARY KEY,
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  spec_name VARCHAR(64) NOT NULL,
  spec_values TEXT NOT NULL
);
COMMENT ON TABLE pd_spu_specs IS 'SPU规格定义';
COMMENT ON COLUMN pd_spu_specs.spec_name IS '规格名(如颜色/尺码)';
COMMENT ON COLUMN pd_spu_specs.spec_values IS '可选值(逗号分隔)';
""",
        """CREATE TABLE IF NOT EXISTS pd_product_favorites (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE pd_product_favorites IS '商品收藏';
""",
        """CREATE TABLE IF NOT EXISTS pd_product_recommends (
  id BIGSERIAL PRIMARY KEY,
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  recommend_product_id BIGINT NOT NULL REFERENCES pd_products(id),
  reason VARCHAR(64),
  score DECIMAL(5,2) DEFAULT 0
);
COMMENT ON TABLE pd_product_recommends IS '商品推荐关联';
COMMENT ON COLUMN pd_product_recommends.reason IS '推荐理由(similar/bought_together)';
COMMENT ON COLUMN pd_product_recommends.score IS '推荐分数';
""",
        """CREATE TABLE IF NOT EXISTS pd_product_comparisons (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  product_ids JSONB NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE pd_product_comparisons IS '商品对比';
COMMENT ON COLUMN pd_product_comparisons.product_ids IS '对比商品ID数组';
""",
    ]

    # ═══ 店铺 (st_) ═══
    lines += [
        "-- ═══════════════════════════════════════════════",
        "-- 店铺 (st_)",
        "-- ═══════════════════════════════════════════════",
        "",
        """CREATE TABLE IF NOT EXISTS st_shops (
  id BIGSERIAL PRIMARY KEY,
  owner_id BIGINT NOT NULL REFERENCES uc_users(id),
  name VARCHAR(128) NOT NULL,
  category_id BIGINT REFERENCES pd_categories(id),
  logo_url VARCHAR(512),
  description TEXT,
  province VARCHAR(32),
  city VARCHAR(32),
  rating DECIMAL(3,2) DEFAULT 5,
  monthly_sales INT DEFAULT 0,
  total_sales INT DEFAULT 0,
  commission_rate DECIMAL(5,4) DEFAULT 0.0500,
  status SMALLINT DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE st_shops IS '店铺表';
COMMENT ON COLUMN st_shops.owner_id IS '店主ID';
COMMENT ON COLUMN st_shops.name IS '店铺名';
COMMENT ON COLUMN st_shops.category_id IS '主营分类';
COMMENT ON COLUMN st_shops.rating IS '店铺评分';
COMMENT ON COLUMN st_shops.monthly_sales IS '月销量';
COMMENT ON COLUMN st_shops.total_sales IS '总销量';
COMMENT ON COLUMN st_shops.commission_rate IS '佣金费率';
COMMENT ON COLUMN st_shops.status IS '状态(0关闭1正常2审核)';
""",
        """CREATE TABLE IF NOT EXISTS st_shop_categories (
  id BIGSERIAL PRIMARY KEY,
  shop_id BIGINT NOT NULL REFERENCES st_shops(id),
  category_id BIGINT NOT NULL REFERENCES pd_categories(id)
);
COMMENT ON TABLE st_shop_categories IS '店铺经营类目';
""",
        """CREATE TABLE IF NOT EXISTS st_shop_rates (
  id BIGSERIAL PRIMARY KEY,
  shop_id BIGINT NOT NULL REFERENCES st_shops(id),
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  rating SMALLINT NOT NULL,
  content TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE st_shop_rates IS '店铺评价';
COMMENT ON COLUMN st_shop_rates.rating IS '评分(1-5)';
""",
        """CREATE TABLE IF NOT EXISTS st_shop_staffs (
  id BIGSERIAL PRIMARY KEY,
  shop_id BIGINT NOT NULL REFERENCES st_shops(id),
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  role VARCHAR(32) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE st_shop_staffs IS '店铺员工';
COMMENT ON COLUMN st_shop_staffs.role IS '角色(owner/manager/customer_service)';
""",
        """CREATE TABLE IF NOT EXISTS st_shop_decorations (
  id BIGSERIAL PRIMARY KEY,
  shop_id BIGINT NOT NULL REFERENCES st_shops(id),
  page_type VARCHAR(32) NOT NULL,
  content JSONB NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE st_shop_decorations IS '店铺装修';
COMMENT ON COLUMN st_shop_decorations.page_type IS '页面类型(home/product_list)';
COMMENT ON COLUMN st_shop_decorations.content IS '装修内容(JSON)';
""",
        """CREATE TABLE IF NOT EXISTS st_shop_qualifications (
  id BIGSERIAL PRIMARY KEY,
  shop_id BIGINT NOT NULL REFERENCES st_shops(id),
  qual_type VARCHAR(64) NOT NULL,
  qual_no VARCHAR(128),
  image_url VARCHAR(512),
  status SMALLINT DEFAULT 0,
  expire_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE st_shop_qualifications IS '店铺资质';
COMMENT ON COLUMN st_shop_qualifications.qual_type IS '资质类型(business_license/food_license)';
COMMENT ON COLUMN st_shop_qualifications.qual_no IS '证件号';
COMMENT ON COLUMN st_shop_qualifications.status IS '状态(0待审1通过2拒绝)';
""",
        """CREATE TABLE IF NOT EXISTS st_shop_settlements (
  id BIGSERIAL PRIMARY KEY,
  shop_id BIGINT NOT NULL REFERENCES st_shops(id),
  period VARCHAR(32) NOT NULL,
  order_count INT NOT NULL DEFAULT 0,
  order_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
  commission DECIMAL(12,2) NOT NULL DEFAULT 0,
  refund_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
  actual_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
  status SMALLINT DEFAULT 0,
  settled_at TIMESTAMP
);
COMMENT ON TABLE st_shop_settlements IS '店铺结算';
COMMENT ON COLUMN st_shop_settlements.period IS '结算周期(如2026-06)';
COMMENT ON COLUMN st_shop_settlements.order_count IS '订单数';
COMMENT ON COLUMN st_shop_settlements.order_amount IS '订单金额';
COMMENT ON COLUMN st_shop_settlements.commission IS '佣金';
COMMENT ON COLUMN st_shop_settlements.refund_amount IS '退款金额';
COMMENT ON COLUMN st_shop_settlements.actual_amount IS '实结金额';
COMMENT ON COLUMN st_shop_settlements.status IS '状态(0待结算1已结算)';
""",
        """CREATE TABLE IF NOT EXISTS st_shop_complaints (
  id BIGSERIAL PRIMARY KEY,
  shop_id BIGINT NOT NULL REFERENCES st_shops(id),
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  reason VARCHAR(256) NOT NULL,
  evidence JSONB,
  status SMALLINT DEFAULT 0,
  handler_id BIGINT,
  result TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE st_shop_complaints IS '店铺投诉';
COMMENT ON COLUMN st_shop_complaints.reason IS '投诉原因';
COMMENT ON COLUMN st_shop_complaints.evidence IS '证据(JSON图片数组)';
COMMENT ON COLUMN st_shop_complaints.status IS '状态(0待处理1处理中2已解决)';
""",
    ]

    # ═══ 订单交易 (biz_) ═══
    lines += [
        "-- ═══════════════════════════════════════════════",
        "-- 订单交易 (biz_)",
        "-- ═══════════════════════════════════════════════",
        "",
        """CREATE TABLE IF NOT EXISTS biz_orders (
  id BIGSERIAL PRIMARY KEY,
  order_no VARCHAR(32) NOT NULL UNIQUE,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  shop_id BIGINT NOT NULL REFERENCES st_shops(id),
  total_amount DECIMAL(12,2) NOT NULL,
  discount_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
  freight_amount DECIMAL(10,2) NOT NULL DEFAULT 0,
  actual_amount DECIMAL(12,2) NOT NULL,
  coupon_id BIGINT,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  payment_method VARCHAR(32),
  paid_at TIMESTAMP,
  shipped_at TIMESTAMP,
  received_at TIMESTAMP,
  buyer_remark VARCHAR(512),
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE biz_orders IS '订单表';
COMMENT ON COLUMN biz_orders.order_no IS '订单编号';
COMMENT ON COLUMN biz_orders.total_amount IS '商品总额';
COMMENT ON COLUMN biz_orders.discount_amount IS '优惠金额';
COMMENT ON COLUMN biz_orders.freight_amount IS '运费';
COMMENT ON COLUMN biz_orders.actual_amount IS '实付金额';
COMMENT ON COLUMN biz_orders.coupon_id IS '使用优惠券ID';
COMMENT ON COLUMN biz_orders.status IS '状态(pending/paid/shipped/delivered/cancelled/refunded)';
COMMENT ON COLUMN biz_orders.payment_method IS '支付方式';
COMMENT ON COLUMN biz_orders.paid_at IS '支付时间';
COMMENT ON COLUMN biz_orders.shipped_at IS '发货时间';
COMMENT ON COLUMN biz_orders.received_at IS '收货时间';
COMMENT ON COLUMN biz_orders.buyer_remark IS '买家备注';
""",
        """CREATE TABLE IF NOT EXISTS biz_order_items (
  id BIGSERIAL PRIMARY KEY,
  order_id BIGINT NOT NULL REFERENCES biz_orders(id),
  sku_id BIGINT NOT NULL REFERENCES pd_product_skus(id),
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  product_name VARCHAR(256),
  sku_spec VARCHAR(256),
  price DECIMAL(12,2) NOT NULL,
  quantity INT NOT NULL,
  subtotal DECIMAL(12,2) NOT NULL,
  refund_status SMALLINT DEFAULT 0
);
COMMENT ON TABLE biz_order_items IS '订单明细';
COMMENT ON COLUMN biz_order_items.product_name IS '商品名称快照';
COMMENT ON COLUMN biz_order_items.sku_spec IS 'SKU规格快照';
COMMENT ON COLUMN biz_order_items.price IS '单价快照';
COMMENT ON COLUMN biz_order_items.quantity IS '数量';
COMMENT ON COLUMN biz_order_items.subtotal IS '小计';
COMMENT ON COLUMN biz_order_items.refund_status IS '退款状态(0无1申请中2已退)';
""",
        """CREATE TABLE IF NOT EXISTS biz_order_payments (
  id BIGSERIAL PRIMARY KEY,
  order_id BIGINT NOT NULL REFERENCES biz_orders(id),
  payment_no VARCHAR(64),
  payment_method VARCHAR(32) NOT NULL,
  amount DECIMAL(12,2) NOT NULL,
  status SMALLINT NOT NULL DEFAULT 0,
  paid_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE biz_order_payments IS '支付记录';
COMMENT ON COLUMN biz_order_payments.payment_no IS '支付流水号';
COMMENT ON COLUMN biz_order_payments.payment_method IS '支付方式';
COMMENT ON COLUMN biz_order_payments.amount IS '支付金额';
COMMENT ON COLUMN biz_order_payments.status IS '状态(0待支付1成功2失败3已退款)';
""",
        """CREATE TABLE IF NOT EXISTS biz_order_refunds (
  id BIGSERIAL PRIMARY KEY,
  order_id BIGINT NOT NULL REFERENCES biz_orders(id),
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  refund_no VARCHAR(32) NOT NULL UNIQUE,
  refund_type VARCHAR(32) NOT NULL,
  reason VARCHAR(256),
  total_amount DECIMAL(12,2) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  approved_by BIGINT,
  approved_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE biz_order_refunds IS '退款单';
COMMENT ON COLUMN biz_order_refunds.refund_no IS '退款单号';
COMMENT ON COLUMN biz_order_refunds.refund_type IS '类型(refund_only/return_refund)';
COMMENT ON COLUMN biz_order_refunds.reason IS '退款原因';
COMMENT ON COLUMN biz_order_refunds.total_amount IS '退款金额';
COMMENT ON COLUMN biz_order_refunds.status IS '状态(pending/approved/rejected/completed)';
""",
        """CREATE TABLE IF NOT EXISTS biz_refund_items (
  id BIGSERIAL PRIMARY KEY,
  refund_id BIGINT NOT NULL REFERENCES biz_order_refunds(id),
  order_item_id BIGINT NOT NULL REFERENCES biz_order_items(id),
  quantity INT NOT NULL,
  amount DECIMAL(12,2) NOT NULL
);
COMMENT ON TABLE biz_refund_items IS '退款明细';
COMMENT ON COLUMN biz_refund_items.quantity IS '退款数量';
COMMENT ON COLUMN biz_refund_items.amount IS '退款金额';
""",
        """CREATE TABLE IF NOT EXISTS biz_order_logistics (
  id BIGSERIAL PRIMARY KEY,
  order_id BIGINT NOT NULL REFERENCES biz_orders(id),
  logistics_company VARCHAR(64) NOT NULL,
  tracking_no VARCHAR(64) NOT NULL,
  status VARCHAR(32) DEFAULT 'pending',
  shipped_at TIMESTAMP,
  received_at TIMESTAMP
);
COMMENT ON TABLE biz_order_logistics IS '物流信息';
COMMENT ON COLUMN biz_order_logistics.logistics_company IS '物流公司';
COMMENT ON COLUMN biz_order_logistics.tracking_no IS '运单号';
COMMENT ON COLUMN biz_order_logistics.status IS '状态(pending/in_transit/delivered)';
""",
        """CREATE TABLE IF NOT EXISTS biz_order_addresses (
  id BIGSERIAL PRIMARY KEY,
  order_id BIGINT NOT NULL REFERENCES biz_orders(id),
  receiver_name VARCHAR(64) NOT NULL,
  phone VARCHAR(20) NOT NULL,
  province VARCHAR(32) NOT NULL,
  city VARCHAR(32) NOT NULL,
  district VARCHAR(32) NOT NULL,
  detail_address VARCHAR(256) NOT NULL
);
COMMENT ON TABLE biz_order_addresses IS '订单收货地址快照';
COMMENT ON COLUMN biz_order_addresses.receiver_name IS '收件人';
COMMENT ON COLUMN biz_order_addresses.phone IS '电话';
""",
        """CREATE TABLE IF NOT EXISTS biz_order_promotions (
  id BIGSERIAL PRIMARY KEY,
  order_id BIGINT NOT NULL REFERENCES biz_orders(id),
  promotion_id BIGINT,
  promotion_type VARCHAR(32),
  discount_amount DECIMAL(10,2) NOT NULL DEFAULT 0
);
COMMENT ON TABLE biz_order_promotions IS '订单优惠明细';
COMMENT ON COLUMN biz_order_promotions.promotion_type IS '优惠类型(coupon/full_reduction/flash)';
COMMENT ON COLUMN biz_order_promotions.discount_amount IS '优惠金额';
""",
        """CREATE TABLE IF NOT EXISTS biz_cart_items (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  sku_id BIGINT NOT NULL REFERENCES pd_product_skus(id),
  quantity INT NOT NULL DEFAULT 1,
  checked BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE biz_cart_items IS '购物车';
COMMENT ON COLUMN biz_cart_items.quantity IS '数量';
COMMENT ON COLUMN biz_cart_items.checked IS '是否选中';
""",
        """CREATE TABLE IF NOT EXISTS biz_coupons (
  id BIGSERIAL PRIMARY KEY,
  shop_id BIGINT REFERENCES st_shops(id),
  name VARCHAR(128) NOT NULL,
  coupon_type VARCHAR(32) NOT NULL,
  discount_value DECIMAL(10,2) NOT NULL,
  min_order_amount DECIMAL(10,2) DEFAULT 0,
  total_count INT NOT NULL,
  used_count INT DEFAULT 0,
  start_at TIMESTAMP NOT NULL,
  end_at TIMESTAMP NOT NULL,
  status SMALLINT DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE biz_coupons IS '优惠券模板';
COMMENT ON COLUMN biz_coupons.coupon_type IS '类型(fixed/percent)';
COMMENT ON COLUMN biz_coupons.discount_value IS '优惠额/折扣率';
COMMENT ON COLUMN biz_coupons.min_order_amount IS '最低订单金额';
COMMENT ON COLUMN biz_coupons.total_count IS '发行总量';
COMMENT ON COLUMN biz_coupons.used_count IS '已使用量';
""",
        """CREATE TABLE IF NOT EXISTS biz_user_coupons (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  coupon_id BIGINT NOT NULL REFERENCES biz_coupons(id),
  status VARCHAR(16) DEFAULT 'unused',
  used_at TIMESTAMP,
  order_id BIGINT,
  received_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE biz_user_coupons IS '用户优惠券';
COMMENT ON COLUMN biz_user_coupons.status IS '状态(unused/used/expired)';
""",
        """CREATE TABLE IF NOT EXISTS biz_pre_orders (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  deposit DECIMAL(10,2) NOT NULL,
  status VARCHAR(16) DEFAULT 'pending',
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE biz_pre_orders IS '预售订单';
COMMENT ON COLUMN biz_pre_orders.deposit IS '定金';
""",
        """CREATE TABLE IF NOT EXISTS biz_order_remarks (
  id BIGSERIAL PRIMARY KEY,
  order_id BIGINT NOT NULL REFERENCES biz_orders(id),
  staff_id BIGINT NOT NULL,
  content TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE biz_order_remarks IS '订单备注(内部)';
COMMENT ON COLUMN biz_order_remarks.staff_id IS '操作人ID';
COMMENT ON COLUMN biz_order_remarks.content IS '备注内容';
""",
        """CREATE TABLE IF NOT EXISTS biz_order_status_logs (
  id BIGSERIAL PRIMARY KEY,
  order_id BIGINT NOT NULL REFERENCES biz_orders(id),
  old_status VARCHAR(32),
  new_status VARCHAR(32) NOT NULL,
  operator VARCHAR(64),
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE biz_order_status_logs IS '订单状态变更日志';
COMMENT ON COLUMN biz_order_status_logs.old_status IS '原状态';
COMMENT ON COLUMN biz_order_status_logs.new_status IS '新状态';
COMMENT ON COLUMN biz_order_status_logs.operator IS '操作人';
""",
    ]

    # ═══ 营销 (mkt_) ═══
    lines += [
        "-- ═══════════════════════════════════════════════",
        "-- 营销 (mkt_)",
        "-- ═══════════════════════════════════════════════",
        "",
        """CREATE TABLE IF NOT EXISTS mkt_promotions (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(128) NOT NULL,
  promotion_type VARCHAR(32) NOT NULL,
  shop_id BIGINT REFERENCES st_shops(id),
  start_at TIMESTAMP NOT NULL,
  end_at TIMESTAMP NOT NULL,
  status SMALLINT DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE mkt_promotions IS '促销活动';
COMMENT ON COLUMN mkt_promotions.promotion_type IS '类型(flash/full_reduction/group_buy/general)';
""",
        """CREATE TABLE IF NOT EXISTS mkt_promotion_rules (
  id BIGSERIAL PRIMARY KEY,
  promotion_id BIGINT NOT NULL REFERENCES mkt_promotions(id),
  rule_type VARCHAR(32) NOT NULL,
  rule_value JSONB NOT NULL
);
COMMENT ON TABLE mkt_promotion_rules IS '促销规则';
COMMENT ON COLUMN mkt_promotion_rules.rule_type IS '规则类型(threshold/discount/gift)';
COMMENT ON COLUMN mkt_promotion_rules.rule_value IS '规则值(JSON)';
""",
        """CREATE TABLE IF NOT EXISTS mkt_promotion_products (
  id BIGSERIAL PRIMARY KEY,
  promotion_id BIGINT NOT NULL REFERENCES mkt_promotions(id),
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  promotion_price DECIMAL(12,2),
  sort_order INT DEFAULT 0
);
COMMENT ON TABLE mkt_promotion_products IS '促销商品关联';
COMMENT ON COLUMN mkt_promotion_products.promotion_price IS '促销价';
""",
        """CREATE TABLE IF NOT EXISTS mkt_flash_sales (
  id BIGSERIAL PRIMARY KEY,
  promotion_id BIGINT NOT NULL REFERENCES mkt_promotions(id),
  start_at TIMESTAMP NOT NULL,
  end_at TIMESTAMP NOT NULL,
  status SMALLINT DEFAULT 1
);
COMMENT ON TABLE mkt_flash_sales IS '秒杀时段';
""",
        """CREATE TABLE IF NOT EXISTS mkt_flash_sale_items (
  id BIGSERIAL PRIMARY KEY,
  flash_sale_id BIGINT NOT NULL REFERENCES mkt_flash_sales(id),
  sku_id BIGINT NOT NULL REFERENCES pd_product_skus(id),
  flash_price DECIMAL(12,2) NOT NULL,
  total_stock INT NOT NULL,
  sold_count INT DEFAULT 0,
  limit_per_user INT DEFAULT 1
);
COMMENT ON TABLE mkt_flash_sale_items IS '秒杀商品';
COMMENT ON COLUMN mkt_flash_sale_items.flash_price IS '秒杀价';
COMMENT ON COLUMN mkt_flash_sale_items.total_stock IS '秒杀库存';
COMMENT ON COLUMN mkt_flash_sale_items.sold_count IS '已售数量';
COMMENT ON COLUMN mkt_flash_sale_items.limit_per_user IS '每人限购';
""",
        """CREATE TABLE IF NOT EXISTS mkt_banners (
  id BIGSERIAL PRIMARY KEY,
  position_id BIGINT,
  title VARCHAR(128) NOT NULL,
  image_url VARCHAR(512) NOT NULL,
  link_url VARCHAR(512),
  sort_order INT DEFAULT 0,
  start_at TIMESTAMP,
  end_at TIMESTAMP,
  status SMALLINT DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE mkt_banners IS '轮播图';
""",
        """CREATE TABLE IF NOT EXISTS mkt_banner_positions (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(64) NOT NULL,
  code VARCHAR(32) NOT NULL UNIQUE,
  width INT,
  height INT
);
COMMENT ON TABLE mkt_banner_positions IS '广告位';
""",
        """CREATE TABLE IF NOT EXISTS mkt_full_reductions (
  id BIGSERIAL PRIMARY KEY,
  promotion_id BIGINT NOT NULL REFERENCES mkt_promotions(id),
  threshold_amount DECIMAL(12,2) NOT NULL,
  discount_amount DECIMAL(12,2) NOT NULL
);
COMMENT ON TABLE mkt_full_reductions IS '满减规则';
COMMENT ON COLUMN mkt_full_reductions.threshold_amount IS '满额门槛';
COMMENT ON COLUMN mkt_full_reductions.discount_amount IS '减免金额';
""",
        """CREATE TABLE IF NOT EXISTS mkt_group_buys (
  id BIGSERIAL PRIMARY KEY,
  promotion_id BIGINT NOT NULL REFERENCES mkt_promotions(id),
  group_price DECIMAL(12,2) NOT NULL,
  min_members INT NOT NULL DEFAULT 2,
  max_members INT DEFAULT 10,
  expire_hours INT DEFAULT 24
);
COMMENT ON TABLE mkt_group_buys IS '拼团活动';
COMMENT ON COLUMN mkt_group_buys.group_price IS '拼团价';
COMMENT ON COLUMN mkt_group_buys.min_members IS '最低成团人数';
COMMENT ON COLUMN mkt_group_buys.max_members IS '最大成团人数';
COMMENT ON COLUMN mkt_group_buys.expire_hours IS '成团时限(小时)';
""",
        """CREATE TABLE IF NOT EXISTS mkt_group_buy_items (
  id BIGSERIAL PRIMARY KEY,
  group_buy_id BIGINT NOT NULL REFERENCES mkt_group_buys(id),
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  order_id BIGINT REFERENCES biz_orders(id),
  role VARCHAR(16) DEFAULT 'member',
  status VARCHAR(16) DEFAULT 'pending',
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE mkt_group_buy_items IS '拼团参与记录';
COMMENT ON COLUMN mkt_group_buy_items.role IS '角色(leader/member)';
""",
        """CREATE TABLE IF NOT EXISTS mkt_point_mall_items (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(128) NOT NULL,
  image_url VARCHAR(512),
  points_required INT NOT NULL,
  stock INT NOT NULL,
  exchanged_count INT DEFAULT 0,
  status SMALLINT DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE mkt_point_mall_items IS '积分商城商品';
COMMENT ON COLUMN mkt_point_mall_items.points_required IS '所需积分';
COMMENT ON COLUMN mkt_point_mall_items.stock IS '库存';
COMMENT ON COLUMN mkt_point_mall_items.exchanged_count IS '已兑换数量';
""",
    ]

    # ═══ 搜索推荐 (sr_) ═══
    lines += [
        "-- ═══════════════════════════════════════════════",
        "-- 搜索推荐 (sr_)",
        "-- ═══════════════════════════════════════════════",
        "",
        """CREATE TABLE IF NOT EXISTS sr_search_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT REFERENCES uc_users(id),
  keyword VARCHAR(128) NOT NULL,
  result_count INT DEFAULT 0,
  clicked_product_id BIGINT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE sr_search_logs IS '搜索日志';
COMMENT ON COLUMN sr_search_logs.keyword IS '搜索词';
COMMENT ON COLUMN sr_search_logs.result_count IS '结果数';
COMMENT ON COLUMN sr_search_logs.clicked_product_id IS '点击商品ID';
""",
        """CREATE TABLE IF NOT EXISTS sr_hot_keywords (
  id BIGSERIAL PRIMARY KEY,
  keyword VARCHAR(128) NOT NULL,
  search_count INT NOT NULL DEFAULT 0,
  period VARCHAR(16) NOT NULL,
  rank INT,
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE sr_hot_keywords IS '热搜词';
COMMENT ON COLUMN sr_hot_keywords.search_count IS '搜索次数';
COMMENT ON COLUMN sr_hot_keywords.period IS '统计周期(daily/weekly/monthly)';
""",
        """CREATE TABLE IF NOT EXISTS sr_search_suggestions (
  id BIGSERIAL PRIMARY KEY,
  keyword VARCHAR(128) NOT NULL,
  suggestion VARCHAR(128) NOT NULL,
  score DECIMAL(5,2) DEFAULT 0
);
COMMENT ON TABLE sr_search_suggestions IS '搜索联想';
COMMENT ON COLUMN sr_search_suggestions.suggestion IS '联想词';
COMMENT ON COLUMN sr_search_suggestions.score IS '权重';
""",
        """CREATE TABLE IF NOT EXISTS sr_recommend_pools (
  id BIGSERIAL PRIMARY KEY,
  pool_name VARCHAR(64) NOT NULL,
  pool_type VARCHAR(32) NOT NULL,
  product_ids JSONB,
  status SMALLINT DEFAULT 1,
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE sr_recommend_pools IS '推荐池';
COMMENT ON COLUMN sr_recommend_pools.pool_name IS '池名(首页/猜你喜欢/相似)';
COMMENT ON COLUMN sr_recommend_pools.pool_type IS '类型(manual/algorithm)';
COMMENT ON COLUMN sr_recommend_pools.product_ids IS '商品ID列表(JSON)';
""",
        """CREATE TABLE IF NOT EXISTS sr_recommend_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  pool_id BIGINT NOT NULL REFERENCES sr_recommend_pools(id),
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  position INT,
  is_clicked BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE sr_recommend_logs IS '推荐曝光日志';
COMMENT ON COLUMN sr_recommend_logs.position IS '推荐位';
COMMENT ON COLUMN sr_recommend_logs.is_clicked IS '是否点击';
""",
        """CREATE TABLE IF NOT EXISTS sr_user_browse_histories (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  browse_duration INT DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE sr_user_browse_histories IS '用户浏览历史';
COMMENT ON COLUMN sr_user_browse_histories.browse_duration IS '浏览时长(秒)';
""",
    ]

    # ═══ 内容 (ct_) ═══
    lines += [
        "-- ═══════════════════════════════════════════════",
        "-- 内容 (ct_)",
        "-- ═══════════════════════════════════════════════",
        "",
        """CREATE TABLE IF NOT EXISTS ct_articles (
  id BIGSERIAL PRIMARY KEY,
  author_id BIGINT NOT NULL REFERENCES uc_users(id),
  category_id BIGINT,
  title VARCHAR(256) NOT NULL,
  content TEXT NOT NULL,
  cover_url VARCHAR(512),
  view_count INT DEFAULT 0,
  like_count INT DEFAULT 0,
  status SMALLINT DEFAULT 1,
  published_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE ct_articles IS '文章';
COMMENT ON COLUMN ct_articles.title IS '标题';
COMMENT ON COLUMN ct_articles.content IS '正文';
COMMENT ON COLUMN ct_articles.view_count IS '阅读量';
COMMENT ON COLUMN ct_articles.like_count IS '点赞数';
""",
        """CREATE TABLE IF NOT EXISTS ct_article_categories (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(64) NOT NULL,
  parent_id BIGINT REFERENCES ct_article_categories(id),
  sort_order INT DEFAULT 0
);
COMMENT ON TABLE ct_article_categories IS '文章分类';
""",
        """CREATE TABLE IF NOT EXISTS ct_article_comments (
  id BIGSERIAL PRIMARY KEY,
  article_id BIGINT NOT NULL REFERENCES ct_articles(id),
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  content TEXT NOT NULL,
  like_count INT DEFAULT 0,
  status SMALLINT DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE ct_article_comments IS '文章评论';
""",
        """CREATE TABLE IF NOT EXISTS ct_topics (
  id BIGSERIAL PRIMARY KEY,
  title VARCHAR(256) NOT NULL,
  description TEXT,
  cover_url VARCHAR(512),
  participant_count INT DEFAULT 0,
  status SMALLINT DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE ct_topics IS '话题';
COMMENT ON COLUMN ct_topics.participant_count IS '参与人数';
""",
        """CREATE TABLE IF NOT EXISTS ct_topic_posts (
  id BIGSERIAL PRIMARY KEY,
  topic_id BIGINT NOT NULL REFERENCES ct_topics(id),
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  content TEXT NOT NULL,
  images JSONB,
  like_count INT DEFAULT 0,
  comment_count INT DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE ct_topic_posts IS '话题帖子';
""",
        """CREATE TABLE IF NOT EXISTS ct_post_comments (
  id BIGSERIAL PRIMARY KEY,
  post_id BIGINT NOT NULL REFERENCES ct_topic_posts(id),
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  content TEXT NOT NULL,
  like_count INT DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE ct_post_comments IS '帖子评论';
""",
        """CREATE TABLE IF NOT EXISTS ct_live_streams (
  id BIGSERIAL PRIMARY KEY,
  shop_id BIGINT NOT NULL REFERENCES st_shops(id),
  title VARCHAR(256) NOT NULL,
  cover_url VARCHAR(512),
  stream_url VARCHAR(512),
  start_at TIMESTAMP NOT NULL,
  end_at TIMESTAMP,
  viewer_count INT DEFAULT 0,
  like_count INT DEFAULT 0,
  status VARCHAR(16) DEFAULT 'upcoming',
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE ct_live_streams IS '直播间';
COMMENT ON COLUMN ct_live_streams.viewer_count IS '观看人数';
COMMENT ON COLUMN ct_live_streams.status IS '状态(upcoming/live/ended)';
""",
        """CREATE TABLE IF NOT EXISTS ct_live_stream_products (
  id BIGSERIAL PRIMARY KEY,
  live_stream_id BIGINT NOT NULL REFERENCES ct_live_streams(id),
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  display_price DECIMAL(12,2),
  sort_order INT DEFAULT 0
);
COMMENT ON TABLE ct_live_stream_products IS '直播商品';
COMMENT ON COLUMN ct_live_stream_products.display_price IS '直播价';
""",
    ]

    # ═══ 客服售后 (cs_) ═══
    lines += [
        "-- ═══════════════════════════════════════════════",
        "-- 客服售后 (cs_)",
        "-- ═══════════════════════════════════════════════",
        "",
        """CREATE TABLE IF NOT EXISTS cs_service_tickets (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  order_id BIGINT REFERENCES biz_orders(id),
  category VARCHAR(64) NOT NULL,
  title VARCHAR(256) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  priority SMALLINT DEFAULT 2,
  source VARCHAR(32) NOT NULL,
  assigned_to BIGINT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE cs_service_tickets IS '工单';
COMMENT ON COLUMN cs_service_tickets.category IS '分类(order/refund/product/other)';
COMMENT ON COLUMN cs_service_tickets.status IS '状态(pending/processing/resolved/closed)';
COMMENT ON COLUMN cs_service_tickets.priority IS '优先级(1高2中3低)';
COMMENT ON COLUMN cs_service_tickets.source IS '来源(web/app/phone/wechat)';
COMMENT ON COLUMN cs_service_tickets.assigned_to IS '处理人ID';
""",
        """CREATE TABLE IF NOT EXISTS cs_ticket_messages (
  id BIGSERIAL PRIMARY KEY,
  ticket_id BIGINT NOT NULL REFERENCES cs_service_tickets(id),
  sender_id BIGINT NOT NULL,
  sender_type VARCHAR(16) NOT NULL,
  content TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE cs_ticket_messages IS '工单消息';
COMMENT ON COLUMN cs_ticket_messages.sender_type IS '发送者类型(user/staff/bot)';
""",
        """CREATE TABLE IF NOT EXISTS cs_after_sales (
  id BIGSERIAL PRIMARY KEY,
  order_id BIGINT NOT NULL REFERENCES biz_orders(id),
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  type VARCHAR(32) NOT NULL,
  reason VARCHAR(256),
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  handler_id BIGINT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE cs_after_sales IS '售后申请';
COMMENT ON COLUMN cs_after_sales.type IS '类型(refund_only/return_refund/exchange)';
""",
        """CREATE TABLE IF NOT EXISTS cs_after_sales_logs (
  id BIGSERIAL PRIMARY KEY,
  after_sale_id BIGINT NOT NULL REFERENCES cs_after_sales(id),
  operator_id BIGINT NOT NULL,
  action VARCHAR(64) NOT NULL,
  remark TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE cs_after_sales_logs IS '售后操作日志';
COMMENT ON COLUMN cs_after_sales_logs.action IS '操作(approve/reject/receive/complete)';
""",
        """CREATE TABLE IF NOT EXISTS cs_complaints (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  target_type VARCHAR(32) NOT NULL,
  target_id BIGINT NOT NULL,
  reason TEXT NOT NULL,
  status VARCHAR(16) DEFAULT 'pending',
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE cs_complaints IS '投诉';
COMMENT ON COLUMN cs_complaints.target_type IS '投诉对象类型(shop/product/user)';
COMMENT ON COLUMN cs_complaints.target_id IS '投诉对象ID';
""",
        """CREATE TABLE IF NOT EXISTS cs_complaint_handlers (
  id BIGSERIAL PRIMARY KEY,
  complaint_id BIGINT NOT NULL REFERENCES cs_complaints(id),
  handler_id BIGINT NOT NULL,
  result TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE cs_complaint_handlers IS '投诉处理记录';
""",
        """CREATE TABLE IF NOT EXISTS cs_knowledge_base (
  id BIGSERIAL PRIMARY KEY,
  category VARCHAR(64) NOT NULL,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  view_count INT DEFAULT 0,
  helpful_count INT DEFAULT 0,
  status SMALLINT DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE cs_knowledge_base IS '知识库FAQ';
COMMENT ON COLUMN cs_knowledge_base.category IS '分类';
COMMENT ON COLUMN cs_knowledge_base.question IS '问题';
COMMENT ON COLUMN cs_knowledge_base.answer IS '回答';
""",
    ]

    # ═══ 仓储物流 (wms_) ═══
    lines += [
        "-- ═══════════════════════════════════════════════",
        "-- 仓储物流 (wms_)",
        "-- ═══════════════════════════════════════════════",
        "",
        """CREATE TABLE IF NOT EXISTS wms_warehouses (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(128) NOT NULL,
  code VARCHAR(32) NOT NULL UNIQUE,
  province VARCHAR(32) NOT NULL,
  city VARCHAR(32) NOT NULL,
  address VARCHAR(256),
  contact_phone VARCHAR(20),
  status SMALLINT DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE wms_warehouses IS '仓库';
COMMENT ON COLUMN wms_warehouses.code IS '仓库编码';
""",
        """CREATE TABLE IF NOT EXISTS wms_warehouse_zones (
  id BIGSERIAL PRIMARY KEY,
  warehouse_id BIGINT NOT NULL REFERENCES wms_warehouses(id),
  zone_name VARCHAR(64) NOT NULL,
  zone_type VARCHAR(32) NOT NULL
);
COMMENT ON TABLE wms_warehouse_zones IS '库区';
COMMENT ON COLUMN wms_warehouse_zones.zone_type IS '类型(normal/cold/frozen/hazardous)';
""",
        """CREATE TABLE IF NOT EXISTS wms_inventory_records (
  id BIGSERIAL PRIMARY KEY,
  sku_id BIGINT NOT NULL REFERENCES pd_product_skus(id),
  warehouse_id BIGINT NOT NULL REFERENCES wms_warehouses(id),
  quantity INT NOT NULL,
  change_type VARCHAR(32) NOT NULL,
  reference_no VARCHAR(64),
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE wms_inventory_records IS '库存变更记录';
COMMENT ON COLUMN wms_inventory_records.quantity IS '变动数量(正入负出)';
COMMENT ON COLUMN wms_inventory_records.change_type IS '类型(inbound/outbound/adjust/return)';
COMMENT ON COLUMN wms_inventory_records.reference_no IS '关联单号';
""",
        """CREATE TABLE IF NOT EXISTS wms_inbound_orders (
  id BIGSERIAL PRIMARY KEY,
  warehouse_id BIGINT NOT NULL REFERENCES wms_warehouses(id),
  supplier_id BIGINT,
  order_no VARCHAR(32) NOT NULL UNIQUE,
  total_quantity INT NOT NULL DEFAULT 0,
  status VARCHAR(16) DEFAULT 'pending',
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE wms_inbound_orders IS '入库单';
COMMENT ON COLUMN wms_inbound_orders.order_no IS '入库单号';
COMMENT ON COLUMN wms_inbound_orders.status IS '状态(pending/partial/completed)';
""",
        """CREATE TABLE IF NOT EXISTS wms_inbound_items (
  id BIGSERIAL PRIMARY KEY,
  inbound_order_id BIGINT NOT NULL REFERENCES wms_inbound_orders(id),
  sku_id BIGINT NOT NULL REFERENCES pd_product_skus(id),
  expected_qty INT NOT NULL,
  actual_qty INT DEFAULT 0
);
COMMENT ON TABLE wms_inbound_items IS '入库明细';
COMMENT ON COLUMN wms_inbound_items.expected_qty IS '预期数量';
COMMENT ON COLUMN wms_inbound_items.actual_qty IS '实际数量';
""",
        """CREATE TABLE IF NOT EXISTS wms_outbound_orders (
  id BIGSERIAL PRIMARY KEY,
  warehouse_id BIGINT NOT NULL REFERENCES wms_warehouses(id),
  order_id BIGINT REFERENCES biz_orders(id),
  order_no VARCHAR(32) NOT NULL UNIQUE,
  total_quantity INT NOT NULL DEFAULT 0,
  status VARCHAR(16) DEFAULT 'pending',
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE wms_outbound_orders IS '出库单';
COMMENT ON COLUMN wms_outbound_orders.order_no IS '出库单号';
""",
        """CREATE TABLE IF NOT EXISTS wms_outbound_items (
  id BIGSERIAL PRIMARY KEY,
  outbound_order_id BIGINT NOT NULL REFERENCES wms_outbound_orders(id),
  sku_id BIGINT NOT NULL REFERENCES pd_product_skus(id),
  quantity INT NOT NULL
);
COMMENT ON TABLE wms_outbound_items IS '出库明细';
""",
        """CREATE TABLE IF NOT EXISTS wms_logistics_companies (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(64) NOT NULL,
  code VARCHAR(32) NOT NULL UNIQUE,
  contact_phone VARCHAR(20),
  status SMALLINT DEFAULT 1
);
COMMENT ON TABLE wms_logistics_companies IS '物流公司';
""",
        """CREATE TABLE IF NOT EXISTS wms_shipping_templates (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(128) NOT NULL,
  charge_type VARCHAR(16) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE wms_shipping_templates IS '运费模板';
COMMENT ON COLUMN wms_shipping_templates.charge_type IS '计费方式(piece/weight/volume)';
""",
        """CREATE TABLE IF NOT EXISTS wms_shipping_template_items (
  id BIGSERIAL PRIMARY KEY,
  template_id BIGINT NOT NULL REFERENCES wms_shipping_templates(id),
  destination VARCHAR(256) NOT NULL,
  first_unit DECIMAL(10,2) NOT NULL,
  first_price DECIMAL(10,2) NOT NULL,
  additional_unit DECIMAL(10,2) NOT NULL,
  additional_price DECIMAL(10,2) NOT NULL
);
COMMENT ON TABLE wms_shipping_template_items IS '运费模板明细';
COMMENT ON COLUMN wms_shipping_template_items.destination IS '目的地(省份/全国)';
COMMENT ON COLUMN wms_shipping_template_items.first_unit IS '首件/首重';
COMMENT ON COLUMN wms_shipping_template_items.first_price IS '首费';
COMMENT ON COLUMN wms_shipping_template_items.additional_unit IS '续件/续重';
COMMENT ON COLUMN wms_shipping_template_items.additional_price IS '续费';
""",
    ]

    # ═══ 供应商 (sup_) ═══
    lines += [
        "-- ═══════════════════════════════════════════════",
        "-- 供应商 (sup_)",
        "-- ═══════════════════════════════════════════════",
        "",
        """CREATE TABLE IF NOT EXISTS sup_suppliers (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(128) NOT NULL,
  contact_person VARCHAR(64),
  contact_phone VARCHAR(20),
  province VARCHAR(32),
  city VARCHAR(32),
  address VARCHAR(256),
  cooperation_start DATE,
  status SMALLINT DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE sup_suppliers IS '供应商';
""",
        """CREATE TABLE IF NOT EXISTS sup_supplier_products (
  id BIGSERIAL PRIMARY KEY,
  supplier_id BIGINT NOT NULL REFERENCES sup_suppliers(id),
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  supply_price DECIMAL(12,2) NOT NULL,
  min_order_qty INT DEFAULT 1,
  lead_days INT DEFAULT 7
);
COMMENT ON TABLE sup_supplier_products IS '供应商商品';
COMMENT ON COLUMN sup_supplier_products.supply_price IS '供货价';
COMMENT ON COLUMN sup_supplier_products.min_order_qty IS '起订量';
COMMENT ON COLUMN sup_supplier_products.lead_days IS '交货天数';
""",
        """CREATE TABLE IF NOT EXISTS sup_purchase_orders (
  id BIGSERIAL PRIMARY KEY,
  supplier_id BIGINT NOT NULL REFERENCES sup_suppliers(id),
  warehouse_id BIGINT NOT NULL REFERENCES wms_warehouses(id),
  order_no VARCHAR(32) NOT NULL UNIQUE,
  total_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
  status VARCHAR(16) DEFAULT 'pending',
  expected_date DATE,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE sup_purchase_orders IS '采购单';
""",
        """CREATE TABLE IF NOT EXISTS sup_purchase_items (
  id BIGSERIAL PRIMARY KEY,
  purchase_order_id BIGINT NOT NULL REFERENCES sup_purchase_orders(id),
  sku_id BIGINT NOT NULL REFERENCES pd_product_skus(id),
  quantity INT NOT NULL,
  unit_price DECIMAL(12,2) NOT NULL,
  subtotal DECIMAL(12,2) NOT NULL
);
COMMENT ON TABLE sup_purchase_items IS '采购明细';
""",
        """CREATE TABLE IF NOT EXISTS sup_supplier_settlements (
  id BIGSERIAL PRIMARY KEY,
  supplier_id BIGINT NOT NULL REFERENCES sup_suppliers(id),
  period VARCHAR(32) NOT NULL,
  purchase_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
  paid_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
  status SMALLINT DEFAULT 0,
  settled_at TIMESTAMP
);
COMMENT ON TABLE sup_supplier_settlements IS '供应商结算';
""",
        """CREATE TABLE IF NOT EXISTS sup_supplier_rates (
  id BIGSERIAL PRIMARY KEY,
  supplier_id BIGINT NOT NULL REFERENCES sup_suppliers(id),
  rater_id BIGINT NOT NULL,
  quality_score SMALLINT,
  delivery_score SMALLINT,
  service_score SMALLINT,
  remark TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE sup_supplier_rates IS '供应商评分';
""",
    ]

    # ═══ 财务 (fin_) ═══
    lines += [
        "-- ═══════════════════════════════════════════════",
        "-- 财务 (fin_)",
        "-- ═══════════════════════════════════════════════",
        "",
        """CREATE TABLE IF NOT EXISTS fin_settlements (
  id BIGSERIAL PRIMARY KEY,
  shop_id BIGINT NOT NULL REFERENCES st_shops(id),
  period VARCHAR(32) NOT NULL,
  order_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
  commission DECIMAL(12,2) NOT NULL DEFAULT 0,
  refund_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
  penalty DECIMAL(12,2) NOT NULL DEFAULT 0,
  actual_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
  status VARCHAR(16) DEFAULT 'pending',
  settled_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE fin_settlements IS '平台结算单';
COMMENT ON COLUMN fin_settlements.period IS '结算周期';
COMMENT ON COLUMN fin_settlements.order_amount IS '订单金额';
COMMENT ON COLUMN fin_settlements.commission IS '平台佣金';
COMMENT ON COLUMN fin_settlements.refund_amount IS '退款金额';
COMMENT ON COLUMN fin_settlements.penalty IS '罚款';
COMMENT ON COLUMN fin_settlements.actual_amount IS '实结金额';
""",
        """CREATE TABLE IF NOT EXISTS fin_settlement_items (
  id BIGSERIAL PRIMARY KEY,
  settlement_id BIGINT NOT NULL REFERENCES fin_settlements(id),
  order_id BIGINT NOT NULL REFERENCES biz_orders(id),
  order_amount DECIMAL(12,2) NOT NULL,
  commission DECIMAL(12,2) NOT NULL,
  refund_amount DECIMAL(12,2) DEFAULT 0
);
COMMENT ON TABLE fin_settlement_items IS '结算明细';
""",
        """CREATE TABLE IF NOT EXISTS fin_invoices (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES uc_users(id),
  order_id BIGINT REFERENCES biz_orders(id),
  invoice_type VARCHAR(16) NOT NULL,
  title VARCHAR(256) NOT NULL,
  tax_no VARCHAR(32),
  amount DECIMAL(12,2) NOT NULL,
  status VARCHAR(16) DEFAULT 'pending',
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE fin_invoices IS '发票';
COMMENT ON COLUMN fin_invoices.invoice_type IS '类型(personal/company)';
COMMENT ON COLUMN fin_invoices.title IS '抬头';
COMMENT ON COLUMN fin_invoices.tax_no IS '税号';
""",
        """CREATE TABLE IF NOT EXISTS fin_invoice_items (
  id BIGSERIAL PRIMARY KEY,
  invoice_id BIGINT NOT NULL REFERENCES fin_invoices(id),
  product_name VARCHAR(256) NOT NULL,
  quantity INT NOT NULL,
  unit_price DECIMAL(12,2) NOT NULL,
  amount DECIMAL(12,2) NOT NULL
);
COMMENT ON TABLE fin_invoice_items IS '发票明细';
""",
        """CREATE TABLE IF NOT EXISTS fin_transactions (
  id BIGSERIAL PRIMARY KEY,
  transaction_no VARCHAR(64) NOT NULL UNIQUE,
  user_id BIGINT REFERENCES uc_users(id),
  shop_id BIGINT REFERENCES st_shops(id),
  amount DECIMAL(12,2) NOT NULL,
  transaction_type VARCHAR(32) NOT NULL,
  payment_method VARCHAR(32),
  status VARCHAR(16) DEFAULT 'pending',
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE fin_transactions IS '资金流水';
COMMENT ON COLUMN fin_transactions.transaction_type IS '类型(payment/refund/withdrawal/settlement)';
""",
        """CREATE TABLE IF NOT EXISTS fin_withdraw_records (
  id BIGSERIAL PRIMARY KEY,
  shop_id BIGINT NOT NULL REFERENCES st_shops(id),
  amount DECIMAL(12,2) NOT NULL,
  bank_name VARCHAR(64),
  bank_account VARCHAR(64),
  status VARCHAR(16) DEFAULT 'pending',
  approved_by BIGINT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE fin_withdraw_records IS '提现记录';
""",
        """CREATE TABLE IF NOT EXISTS fin_commission_rules (
  id BIGSERIAL PRIMARY KEY,
  category_id BIGINT REFERENCES pd_categories(id),
  min_rate DECIMAL(5,4) NOT NULL,
  max_rate DECIMAL(5,4) NOT NULL,
  current_rate DECIMAL(5,4) NOT NULL,
  effective_from DATE NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE fin_commission_rules IS '佣金规则';
COMMENT ON COLUMN fin_commission_rules.min_rate IS '最低费率';
COMMENT ON COLUMN fin_commission_rules.max_rate IS '最高费率';
COMMENT ON COLUMN fin_commission_rules.current_rate IS '当前费率';
""",
        """CREATE TABLE IF NOT EXISTS fin_platform_revenue (
  id BIGSERIAL PRIMARY KEY,
  period DATE NOT NULL,
  commission_income DECIMAL(12,2) NOT NULL DEFAULT 0,
  ad_income DECIMAL(12,2) NOT NULL DEFAULT 0,
  service_income DECIMAL(12,2) NOT NULL DEFAULT 0,
  other_income DECIMAL(12,2) NOT NULL DEFAULT 0,
  total_income DECIMAL(12,2) NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE fin_platform_revenue IS '平台收入日报';
""",
    ]

    # ═══ 运营分析 (ops_) ═══
    lines += [
        "-- ═══════════════════════════════════════════════",
        "-- 运营分析 (ops_)",
        "-- ═══════════════════════════════════════════════",
        "",
        """CREATE TABLE IF NOT EXISTS ops_daily_reports (
  id BIGSERIAL PRIMARY KEY,
  report_date DATE NOT NULL,
  total_users INT DEFAULT 0,
  new_users INT DEFAULT 0,
  active_users INT DEFAULT 0,
  total_orders INT DEFAULT 0,
  order_amount DECIMAL(14,2) DEFAULT 0,
  avg_order_amount DECIMAL(10,2) DEFAULT 0,
  refund_rate DECIMAL(5,4) DEFAULT 0,
  conversion_rate DECIMAL(5,4) DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE ops_daily_reports IS '平台日报';
COMMENT ON COLUMN ops_daily_reports.total_users IS '总用户数';
COMMENT ON COLUMN ops_daily_reports.new_users IS '新增用户';
COMMENT ON COLUMN ops_daily_reports.active_users IS '活跃用户';
COMMENT ON COLUMN ops_daily_reports.total_orders IS '总订单数';
COMMENT ON COLUMN ops_daily_reports.order_amount IS '订单总额';
COMMENT ON COLUMN ops_daily_reports.avg_order_amount IS '客单价';
COMMENT ON COLUMN ops_daily_reports.refund_rate IS '退款率';
COMMENT ON COLUMN ops_daily_reports.conversion_rate IS '转化率';
""",
        """CREATE TABLE IF NOT EXISTS ops_shop_daily_reports (
  id BIGSERIAL PRIMARY KEY,
  shop_id BIGINT NOT NULL REFERENCES st_shops(id),
  report_date DATE NOT NULL,
  visitors INT DEFAULT 0,
  orders INT DEFAULT 0,
  order_amount DECIMAL(12,2) DEFAULT 0,
  refund_orders INT DEFAULT 0,
  refund_amount DECIMAL(12,2) DEFAULT 0,
  avg_rating DECIMAL(3,2) DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE ops_shop_daily_reports IS '店铺日报';
""",
        """CREATE TABLE IF NOT EXISTS ops_product_daily_reports (
  id BIGSERIAL PRIMARY KEY,
  product_id BIGINT NOT NULL REFERENCES pd_products(id),
  report_date DATE NOT NULL,
  views INT DEFAULT 0,
  favorites INT DEFAULT 0,
  cart_adds INT DEFAULT 0,
  orders INT DEFAULT 0,
  order_amount DECIMAL(12,2) DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE ops_product_daily_reports IS '商品日报';
""",
        """CREATE TABLE IF NOT EXISTS ops_user_funnel_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT REFERENCES uc_users(id),
  funnel_date DATE NOT NULL,
  step VARCHAR(32) NOT NULL,
  source VARCHAR(32),
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE ops_user_funnel_logs IS '用户漏斗日志';
COMMENT ON COLUMN ops_user_funnel_logs.step IS '步骤(visit/search/detail/cart/order/pay)';
COMMENT ON COLUMN ops_user_funnel_logs.source IS '渠道(organic/paid/social/referral)';
""",
        """CREATE TABLE IF NOT EXISTS ops_page_views (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT REFERENCES uc_users(id),
  page_url VARCHAR(512) NOT NULL,
  page_title VARCHAR(256),
  referer VARCHAR(512),
  duration INT DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE ops_page_views IS '页面浏览';
COMMENT ON COLUMN ops_page_views.duration IS '停留时长(秒)';
""",
        """CREATE TABLE IF NOT EXISTS ops_app_events (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT REFERENCES uc_users(id),
  event_name VARCHAR(64) NOT NULL,
  event_params JSONB,
  platform VARCHAR(16),
  app_version VARCHAR(16),
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE ops_app_events IS 'APP事件';
COMMENT ON COLUMN ops_app_events.event_name IS '事件名';
COMMENT ON COLUMN ops_app_events.event_params IS '事件参数(JSON)';
COMMENT ON COLUMN ops_app_events.platform IS '平台(ios/android/web)';
""",
        """CREATE TABLE IF NOT EXISTS ops_ab_tests (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(128) NOT NULL,
  description TEXT,
  metric VARCHAR(64) NOT NULL,
  start_at TIMESTAMP NOT NULL,
  end_at TIMESTAMP,
  status VARCHAR(16) DEFAULT 'running',
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE ops_ab_tests IS 'AB测试';
COMMENT ON COLUMN ops_ab_tests.metric IS '指标(conversion/retention/revenue)';
""",
        """CREATE TABLE IF NOT EXISTS ops_ab_test_variants (
  id BIGSERIAL PRIMARY KEY,
  test_id BIGINT NOT NULL REFERENCES ops_ab_tests(id),
  variant_name VARCHAR(64) NOT NULL,
  description TEXT,
  traffic_percent DECIMAL(5,2) NOT NULL,
  is_control BOOLEAN DEFAULT FALSE
);
COMMENT ON TABLE ops_ab_test_variants IS 'AB测试变体';
COMMENT ON COLUMN ops_ab_test_variants.traffic_percent IS '流量占比';
COMMENT ON COLUMN ops_ab_test_variants.is_control IS '是否对照组';
""",
    ]

    # ═══ 权限配置 (sys_) ═══
    lines += [
        "-- ═══════════════════════════════════════════════",
        "-- 权限配置 (sys_)",
        "-- ═══════════════════════════════════════════════",
        "",
        """CREATE TABLE IF NOT EXISTS sys_admins (
  id BIGSERIAL PRIMARY KEY,
  username VARCHAR(64) NOT NULL UNIQUE,
  real_name VARCHAR(64),
  phone VARCHAR(20),
  email VARCHAR(128),
  role_id BIGINT,
  status SMALLINT DEFAULT 1,
  last_login_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE sys_admins IS '后台管理员';
""",
        """CREATE TABLE IF NOT EXISTS sys_roles (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(64) NOT NULL UNIQUE,
  description TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE sys_roles IS '角色';
""",
        """CREATE TABLE IF NOT EXISTS sys_permissions (
  id BIGSERIAL PRIMARY KEY,
  parent_id BIGINT REFERENCES sys_permissions(id),
  name VARCHAR(64) NOT NULL,
  code VARCHAR(64) NOT NULL UNIQUE,
  type VARCHAR(16) NOT NULL,
  sort_order INT DEFAULT 0
);
COMMENT ON TABLE sys_permissions IS '权限';
COMMENT ON COLUMN sys_permissions.type IS '类型(menu/button/api)';
""",
        """CREATE TABLE IF NOT EXISTS sys_role_permissions (
  id BIGSERIAL PRIMARY KEY,
  role_id BIGINT NOT NULL REFERENCES sys_roles(id),
  permission_id BIGINT NOT NULL REFERENCES sys_permissions(id)
);
COMMENT ON TABLE sys_role_permissions IS '角色权限关联';
""",
        """CREATE TABLE IF NOT EXISTS sys_operation_logs (
  id BIGSERIAL PRIMARY KEY,
  admin_id BIGINT NOT NULL,
  module VARCHAR(64) NOT NULL,
  action VARCHAR(64) NOT NULL,
  target_type VARCHAR(64),
  target_id BIGINT,
  detail JSONB,
  ip VARCHAR(64),
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE sys_operation_logs IS '操作日志';
COMMENT ON COLUMN sys_operation_logs.module IS '模块';
COMMENT ON COLUMN sys_operation_logs.action IS '操作(create/update/delete/approve)';
""",
    ]

    lines.append("\n-- DDL 完成\n")
    return "\n".join(lines)


# ── DML 生成 ────────────────────────────────────────────────

def dml():
    lines = ["\n-- ═══════════════════════════════════════════════",
             "-- DML: 样本数据",
             "-- ═══════════════════════════════════════════════\n"]

    # ── 用户 ──
    lines.append("INSERT INTO uc_users (username, nickname, phone, email, gender, birthday, status, created_at) VALUES")
    user_vals = []
    for i in range(1, N_USERS + 1):
        name = gen_name()
        gender = random.choice([0, 1, 2])
        lines_i = f"  ('user{i:04d}', '{esc(name)}', '{gen_phone()}', '{esc(gen_email(f'user{i}'))}', {gender}, '{gen_date(start_days=365*30, end_days=365*5)}', 1, '{gen_datetime(start_days=730, end_days=0)}')"
        user_vals.append(lines_i)
    lines.append(",\n".join(user_vals) + ";\n")

    # ── 用户实名 ──
    lines.append("INSERT INTO uc_user_profiles (user_id, real_name, id_card, province, city, district) VALUES")
    profile_vals = []
    for i in range(1, N_USERS + 1):
        name = gen_name()
        profile_vals.append(f"  ({i}, '{esc(name)}', '{random.randint(100000, 999999)}{random.randint(1950, 2005)}{random.randint(10, 12)}{random.randint(10, 31)}{random.randint(1000, 9999)}', '{random.choice(PROVINCES)}', '{random.choice(PROVINCES)}市', '{random.choice(["朝阳区", "海淀区", "浦东新区", "天河区", "武侯区"])}')")
    lines.append(",\n".join(profile_vals) + ";\n")

    # ── 收货地址 ──
    lines.append("INSERT INTO uc_addresses (user_id, receiver_name, phone, province, city, district, detail_address, is_default) VALUES")
    addr_vals = []
    for i in range(1, N_USERS + 1):
        n_addrs = random.randint(1, 3)
        for j in range(n_addrs):
            addr_vals.append(f"  ({i}, '{esc(gen_name())}', '{gen_phone()}', '{random.choice(PROVINCES)}', '{random.choice(PROVINCES)}市', '某区', '某路{random.randint(1, 999)}号', {'true' if j == 0 else 'false'})")
    lines.append(",\n".join(addr_vals) + ";\n")

    # ── 会员等级 ──
    lines.append("INSERT INTO uc_memberships (user_id, level, points, total_spent, expire_at) VALUES")
    mem_vals = []
    for i in range(1, N_USERS + 1):
        level = random.choices([1, 2, 3, 4, 5, 6], weights=[40, 25, 15, 10, 7, 3])[0]
        points = random.randint(0, 50000)
        spent = round(random.uniform(0, 100000), 2)
        mem_vals.append(f"  ({i}, {level}, {points}, {spent}, '{gen_date(start_days=365, end_days=0)}')")
    lines.append(",\n".join(mem_vals) + ";\n")

    # ── 积分账户 ──
    lines.append("INSERT INTO uc_points_accounts (user_id, balance, total_earned, total_spent) VALUES")
    pa_vals = []
    for i in range(1, N_USERS + 1):
        earned = random.randint(0, 50000)
        spent = random.randint(0, earned)
        pa_vals.append(f"  ({i}, {earned - spent}, {earned}, {spent})")
    lines.append(",\n".join(pa_vals) + ";\n")

    # ── 分类 (2级) ──
    lines.append("INSERT INTO pd_categories (name, parent_id, level, sort_order) VALUES")
    cat_vals = []
    cat_id = 1
    l1_ids = {}
    for l1_name in CATEGORIES_L1:
        l1_ids[l1_name] = cat_id
        cat_vals.append(f"  ('{esc(l1_name)}', NULL, 1, {cat_id})")
        cat_id += 1
        for l2_name in CATEGORIES_L2.get(l1_name, []):
            cat_vals.append(f"  ('{esc(l2_name)}', {l1_ids[l1_name]}, 2, {cat_id})")
            cat_id += 1
    lines.append(",\n".join(cat_vals) + ";\n")
    total_cats = cat_id - 1

    # ── 品牌 ──
    lines.append("INSERT INTO pd_brands (name, country, is_verified) VALUES")
    brand_vals = []
    for b in BRANDS:
        brand_vals.append(f"  ('{esc(b)}', '{random.choice(["中国", "日本", "韩国", "美国", "法国", "德国", "英国", "意大利"])}', {'true' if random.random() > 0.3 else 'false'})")
    lines.append(",\n".join(brand_vals) + ";\n")

    # ── 店铺 ──
    lines.append("INSERT INTO st_shops (owner_id, name, category_id, province, city, rating, monthly_sales, total_sales, commission_rate, status) VALUES")
    shop_names = [
        "旗舰店", "官方直营店", "品牌专卖店", "优选商城", "全球购",
        "生活馆", "潮品汇", "品质生活", "好物集", "天天特卖",
        "品牌折扣店", "海外专营店", "时尚潮铺", "美食特产店", "数码旗舰",
        "母婴好店", "家居精选", "运动达人", "美妆小铺", "图书专营",
        "家电城", "珠宝阁", "零食工坊", "鲜果直达", "服饰工厂店",
        "个护精品店", "文具小站", "宠物天地", "花艺生活", "潮玩部落",
    ]
    shop_vals = []
    for i, sn in enumerate(shop_names[:N_SHOPS], 1):
        rating = round(random.uniform(4.0, 5.0), 2)
        monthly = random.randint(100, 10000)
        total = monthly * random.randint(6, 24)
        comm = round(random.uniform(0.02, 0.10), 4)
        shop_vals.append(f"  ({random.randint(1, N_USERS)}, '{esc(sn)}', {random.randint(1, total_cats)}, '{random.choice(PROVINCES)}', '{random.choice(PROVINCES)}市', {rating}, {monthly}, {total}, {comm}, 1)")
    lines.append(",\n".join(shop_vals) + ";\n")

    # ── 商品SPU ──
    lines.append("INSERT INTO pd_products (shop_id, category_id, brand_id, name, price, original_price, status, is_on_sale, sales_count, view_count, avg_rating) VALUES")
    prod_names = [
        "新款", "经典", "限定", "联名", "升级版", "轻奢", "高定",
        "舒适", "简约", "复古", "潮流", "运动", "商务", "休闲",
    ]
    prod_suffixes = ["T恤", "连衣裙", "手机", "耳机", "面霜", "空调", "零食礼包", "跑步鞋", "背包", "笔记本", "口红", "奶粉"]
    prod_vals = []
    for i in range(1, N_PRODUCTS + 1):
        cat_id = random.randint(1, total_cats)
        brand_id = random.randint(1, len(BRANDS))
        shop_id = random.randint(1, N_SHOPS)
        name = f"{random.choice(prod_names)}{random.choice(prod_suffixes)}{random.randint(1, 99)}"
        price = gen_price(10, 5000)
        orig = round(price * random.uniform(1.1, 2.0), 2)
        sales = random.randint(0, 10000)
        views = sales * random.randint(3, 20)
        rating = round(random.uniform(3.5, 5.0), 2)
        prod_vals.append(f"  ({shop_id}, {cat_id}, {brand_id}, '{esc(name)}', {price}, {orig}, 1, {'true' if random.random() > 0.3 else 'false'}, {sales}, {views}, {rating})")
    lines.append(",\n".join(prod_vals) + ";\n")

    # ── SKU ──
    lines.append("INSERT INTO pd_product_skus (product_id, sku_code, spec_values, price, stock, safety_stock, weight, status) VALUES")
    sku_vals = []
    sku_id = 1
    sku_per_product = {}
    for pid in range(1, N_PRODUCTS + 1):
        n_skus = random.randint(1, 3)
        sku_per_product[pid] = []
        for j in range(n_skus):
            color = random.choice(["红色", "蓝色", "黑色", "白色", "绿色", "灰色"])
            size = random.choice(["S", "M", "L", "XL", "XXL", "均码"])
            spec = f'{{"颜色":"{color}","尺码":"{size}"}}'
            price = gen_price(10, 5000)
            stock = random.randint(0, 5000)
            safety = random.randint(5, 50)
            weight = round(random.uniform(0.1, 5.0), 2)
            sku_vals.append(f"  ({pid}, 'SKU{pid:05d}{chr(65+j)}', '{spec}'::jsonb, {price}, {stock}, {safety}, {weight}, 1)")
            sku_per_product[pid].append(sku_id)
            sku_id += 1
    lines.append(",\n".join(sku_vals) + ";\n")
    total_skus = sku_id - 1

    # ── 订单 ──
    lines.append("INSERT INTO biz_orders (order_no, user_id, shop_id, total_amount, discount_amount, freight_amount, actual_amount, status, payment_method, paid_at, shipped_at, received_at, buyer_remark, created_at) VALUES")
    order_vals = []
    for i in range(1, N_ORDERS + 1):
        user_id = random.randint(1, N_USERS)
        shop_id = random.randint(1, N_SHOPS)
        total = gen_price(20, 3000)
        discount = round(total * random.uniform(0, 0.3), 2)
        freight = random.choice([0, 5, 8, 10, 12, 15])
        actual = round(total - discount + freight, 2)
        status = random.choice(ORDER_STATUS)
        pay_method = random.choice(PAYMENT_METHODS) if status != "pending" else "NULL"
        pay_method_sql = f"'{pay_method}'" if pay_method != "NULL" else "NULL"
        paid_at = f"'{gen_datetime(start_days=90, end_days=1)}'" if status in ORDER_STATUS[1:] else "NULL"
        shipped_at = f"'{gen_datetime(start_days=60, end_days=1)}'" if status in ORDER_STATUS[2:] else "NULL"
        received_at = f"'{gen_datetime(start_days=30, end_days=0)}'" if status == "delivered" else "NULL"
        remarks = random.choice(["请尽快发货", "要正品", "", "", "", "", "包装好一点", "送人的"])
        remark_sql = f"'{esc(remarks)}'" if remarks else "NULL"
        order_no = f"ORD{datetime.now().strftime('%Y%m%d')}{i:06d}"
        order_vals.append(
            f"  ('{order_no}', {user_id}, {shop_id}, {total}, {discount}, {freight}, {actual}, '{status}', {pay_method_sql}, {paid_at}, {shipped_at}, {received_at}, {remark_sql}, '{gen_datetime(start_days=90, end_days=0)}')"
        )
    lines.append(",\n".join(order_vals) + ";\n")

    # ── 订单明细 ──
    lines.append("INSERT INTO biz_order_items (order_id, sku_id, product_id, product_name, sku_spec, price, quantity, subtotal) VALUES")
    item_vals = []
    for oid in range(1, N_ORDERS + 1):
        n_items = random.randint(1, 4)
        for _ in range(n_items):
            pid = random.randint(1, N_PRODUCTS)
            skus = sku_per_product.get(pid, [1])
            sid = random.choice(skus)
            price = gen_price(10, 2000)
            qty = random.randint(1, 5)
            subtotal = round(price * qty, 2)
            item_vals.append(f"  ({oid}, {sid}, {pid}, '商品{pid}', '默认规格', {price}, {qty}, {subtotal})")
    lines.append(",\n".join(item_vals) + ";\n")

    # ── 支付记录 ──
    lines.append("INSERT INTO biz_order_payments (order_id, payment_no, payment_method, amount, status, paid_at) VALUES")
    pay_vals = []
    for oid in range(1, N_ORDERS + 1):
        pay_vals.append(f"  ({oid}, 'PAY{oid:08d}', '{random.choice(PAYMENT_METHODS)}', {gen_price(20, 3000)}, 1, '{gen_datetime(start_days=90, end_days=0)}')")
    lines.append(",\n".join(pay_vals) + ";\n")

    # ── 优惠券模板 ──
    lines.append("INSERT INTO biz_coupons (shop_id, name, coupon_type, discount_value, min_order_amount, total_count, used_count, start_at, end_at, status) VALUES")
    coupon_vals = []
    for i in range(1, N_COUPONS + 1):
        shop_id = random.randint(1, N_SHOPS)
        ctype = random.choice(["fixed", "percent"])
        dval = round(random.uniform(5, 100), 2) if ctype == "fixed" else round(random.uniform(0.8, 0.95), 2)
        min_amt = round(random.uniform(50, 500), 2)
        total = random.randint(100, 5000)
        used = random.randint(0, total)
        coupon_vals.append(f"  ({shop_id}, '优惠券{i:03d}', '{ctype}', {dval}, {min_amt}, {total}, {used}, '{gen_datetime(start_days=30, end_days=0)}', '{gen_datetime(start_days=0, end_days=0)}', 1)")
    lines.append(",\n".join(coupon_vals) + ";\n")

    # ── 仓库 ──
    lines.append("INSERT INTO wms_warehouses (name, code, province, city, address, contact_phone, status) VALUES")
    wh_vals = []
    for i, city in enumerate(WAREHOUSE_CITIES, 1):
        wh_vals.append(f"  ('{city}仓', 'WH{city}', '{city}', '{city}', '{city}市物流园区{i}号', '0{random.randint(10, 99)}-{random.randint(1000, 9999)}', 1)")
    lines.append(",\n".join(wh_vals) + ";\n")

    # ── 物流公司 ──
    lines.append("INSERT INTO wms_logistics_companies (name, code, contact_phone, status) VALUES")
    lc_vals = []
    for i, lc in enumerate(LOGISTICS_COMPANIES, 1):
        lc_vals.append(f"  ('{lc}', 'LC{i:03d}', '400{random.randint(100, 999)}{random.randint(1000, 9999)}', 1)")
    lines.append(",\n".join(lc_vals) + ";\n")

    # ── 供应商 ──
    supplier_names = [
        "华南纺织集团", "华东电子科技", "北京日化", "深圳数码供应链", "上海美妆国际",
        "杭州食品工业", "广州服装批发", "成都家居制造", "武汉运动装备", "南京文具集团",
    ]
    lines.append("INSERT INTO sup_suppliers (name, contact_person, contact_phone, province, city, address, cooperation_start, status) VALUES")
    sup_vals = []
    for sn in supplier_names:
        sup_vals.append(f"  ('{esc(sn)}', '{esc(gen_name())}', '{gen_phone()}', '{random.choice(PROVINCES)}', '{random.choice(PROVINCES)}市', '某工业园区{random.randint(1, 99)}号', '{gen_date(start_days=365*3, end_days=365)}', 1)")
    lines.append(",\n".join(sup_vals) + ";\n")

    # ── 物流信息 ──
    lines.append("INSERT INTO biz_order_logistics (order_id, logistics_company, tracking_no, status, shipped_at, received_at) VALUES")
    log_vals = []
    for oid in range(1, min(N_ORDERS + 1, 1500)):
        lc = random.choice(LOGISTICS_COMPANIES)
        tracking = f"SF{random.randint(1000000000, 9999999999)}"
        log_vals.append(f"  ({oid}, '{lc}', '{tracking}', '{random.choice(["pending", "in_transit", "delivered"])}', '{gen_datetime(start_days=60, end_days=1)}', '{gen_datetime(start_days=30, end_days=0)}')")
    lines.append(",\n".join(log_vals) + ";\n")

    # ── 商品评价 ──
    lines.append("INSERT INTO pd_product_reviews (product_id, user_id, rating, content, is_anonymous, like_count, status) VALUES")
    review_contents = [
        "质量很好，非常满意！", "一般般，还行吧", "物流很快，包装也好",
        "性价比很高，推荐购买", "不太满意，和描述有差距", "第二次购买了，一如既往地好",
        "颜色比图片深一点", "尺码偏大/偏小", "面料很舒服", "外观漂亮，做工精细",
    ]
    rev_vals = []
    for i in range(1, 800):
        rev_vals.append(f"  ({random.randint(1, N_PRODUCTS)}, {random.randint(1, N_USERS)}, {random.randint(1, 5)}, '{esc(random.choice(review_contents))}', {'true' if random.random() > 0.5 else 'false'}, {random.randint(0, 200)}, 1)")
    lines.append(",\n".join(rev_vals) + ";\n")

    # ── 搜索日志 ──
    search_keywords = ["连衣裙", "手机", "面膜", "空调", "零食", "跑步鞋", "口红", "笔记本电脑", "婴儿推车", "帐篷",
                       "羽绒服", "保温杯", "充电宝", "洗面奶", "坚果", "耳机", "沙发", "奶粉", "瑜伽垫", "行李箱"]
    lines.append("INSERT INTO sr_search_logs (user_id, keyword, result_count, clicked_product_id, created_at) VALUES")
    sl_vals = []
    for i in range(1, 2000):
        sl_vals.append(f"  ({random.randint(1, N_USERS)}, '{esc(random.choice(search_keywords))}', {random.randint(0, 500)}, {random.choice([f'{random.randint(1, N_PRODUCTS)}', 'NULL'])}, '{gen_datetime(start_days=90, end_days=0)}')")
    lines.append(",\n".join(sl_vals) + ";\n")

    # ── 日报 ──
    lines.append("INSERT INTO ops_daily_reports (report_date, total_users, new_users, active_users, total_orders, order_amount, avg_order_amount, refund_rate, conversion_rate) VALUES")
    dr_vals = []
    base = datetime.now() - timedelta(days=90)
    for d in range(90):
        date = (base + timedelta(days=d)).strftime("%Y-%m-%d")
        tu = 5000 + d * 50 + random.randint(-20, 20)
        nu = random.randint(20, 200)
        au = random.randint(500, 2000)
        to = random.randint(100, 800)
        oa = round(to * random.uniform(80, 300), 2)
        ao = round(oa / to, 2)
        rr = round(random.uniform(0.01, 0.08), 4)
        cr = round(random.uniform(0.02, 0.12), 4)
        dr_vals.append(f"  ('{date}', {tu}, {nu}, {au}, {to}, {oa}, {ao}, {rr}, {cr})")
    lines.append(",\n".join(dr_vals) + ";\n")

    # ── 店铺日报 ──
    lines.append("INSERT INTO ops_shop_daily_reports (shop_id, report_date, visitors, orders, order_amount, refund_orders, refund_amount, avg_rating) VALUES")
    sdr_vals = []
    for sid in range(1, N_SHOPS + 1):
        for d in range(0, 90, 3):  # 每3天一条，减少量
            date = (base + timedelta(days=d)).strftime("%Y-%m-%d")
            vis = random.randint(50, 5000)
            ords = random.randint(5, 300)
            amt = round(ords * random.uniform(50, 500), 2)
            refs = random.randint(0, ords // 5)
            ref_amt = round(refs * random.uniform(30, 200), 2)
            rt = round(random.uniform(4.0, 5.0), 2)
            sdr_vals.append(f"  ({sid}, '{date}', {vis}, {ords}, {amt}, {refs}, {ref_amt}, {rt})")
    lines.append(",\n".join(sdr_vals) + ";\n")

    # ── 库存变更 ──
    lines.append("INSERT INTO wms_inventory_records (sku_id, warehouse_id, quantity, change_type, reference_no, created_at) VALUES")
    inv_vals = []
    for i in range(1, 1500):
        inv_vals.append(f"  ({random.randint(1, total_skus)}, {random.randint(1, len(WAREHOUSE_CITIES))}, {random.choice([random.randint(1, 100), -random.randint(1, 50)])}, '{random.choice(["inbound", "outbound", "adjust", "return"])}', 'REF{i:08d}', '{gen_datetime(start_days=90, end_days=0)}')")
    lines.append(",\n".join(inv_vals) + ";\n")

    # ── 促销活动 ──
    lines.append("INSERT INTO mkt_promotions (name, promotion_type, shop_id, start_at, end_at, status) VALUES")
    promo_names = ["618大促", "双11全球狂欢", "年货节", "开学季", "女神节特惠", "周年庆", "清凉一夏", "金秋焕新", "黑色星期五", "品牌日", "新品尝鲜", "清仓特卖", "会员日", "限时秒杀", "满减狂欢", "拼团特惠", "直播专属", "预售盛典", "跨店满减", "超级品类日"]
    mkt_vals = []
    for i, pn in enumerate(promo_names[:N_PROMOTIONS], 1):
        ptype = random.choice(["flash", "full_reduction", "group_buy", "general"])
        sid = random.choice([f"{random.randint(1, N_SHOPS)}", "NULL"])
        mkt_vals.append(f"  ('{esc(pn)}', '{ptype}', {sid}, '{gen_datetime(start_days=180, end_days=30)}', '{gen_datetime(start_days=30, end_days=0)}', 1)")
    lines.append(",\n".join(mkt_vals) + ";\n")

    # ── 平台收入 ──
    lines.append("INSERT INTO fin_platform_revenue (period, commission_income, ad_income, service_income, other_income, total_income) VALUES")
    fp_vals = []
    for d in range(90):
        date = (base + timedelta(days=d)).strftime("%Y-%m-%d")
        comm = round(random.uniform(5000, 50000), 2)
        ad = round(random.uniform(2000, 20000), 2)
        svc = round(random.uniform(1000, 10000), 2)
        other = round(random.uniform(500, 5000), 2)
        total = round(comm + ad + svc + other, 2)
        fp_vals.append(f"  ('{date}', {comm}, {ad}, {svc}, {other}, {total})")
    lines.append(",\n".join(fp_vals) + ";\n")

    # ── 系统角色/权限/管理员 ──
    lines.append("INSERT INTO sys_roles (name, description) VALUES\n  ('超级管理员', '拥有全部权限'),\n  ('运营人员', '日常运营管理'),\n  ('财务人员', '结算与财务管理'),\n  ('客服人员', '工单与售后处理'),\n  ('数据分析师', '只读数据查看');\n")

    lines.append("INSERT INTO sys_admins (username, real_name, phone, email, role_id, status) VALUES\n  ('admin', '超级管理员', '13800000000', 'admin@chatbi.com', 1, 1),\n  ('operator01', '运营张三', '13800000001', 'op@chatbi.com', 2, 1),\n  ('finance01', '财务李四', '13800000002', 'fin@chatbi.com', 3, 1),\n  ('cs01', '客服王五', '13800000003', 'cs@chatbi.com', 4, 1),\n  ('analyst01', '分析师赵六', '13800000004', 'data@chatbi.com', 5, 1);\n")

    # ── 创建索引 ──
    lines += [
        "\n-- ═══════════════════════════════════════════════",
        "-- 索引",
        "-- ═══════════════════════════════════════════════\n",
        "CREATE INDEX IF NOT EXISTS idx_biz_orders_user ON biz_orders(user_id);",
        "CREATE INDEX IF NOT EXISTS idx_biz_orders_shop ON biz_orders(shop_id);",
        "CREATE INDEX IF NOT EXISTS idx_biz_orders_status ON biz_orders(status);",
        "CREATE INDEX IF NOT EXISTS idx_biz_orders_created ON biz_orders(created_at);",
        "CREATE INDEX IF NOT EXISTS idx_pd_products_shop ON pd_products(shop_id);",
        "CREATE INDEX IF NOT EXISTS idx_pd_products_category ON pd_products(category_id);",
        "CREATE INDEX IF NOT EXISTS idx_pd_products_brand ON pd_products(brand_id);",
        "CREATE INDEX IF NOT EXISTS idx_sr_search_logs_user ON sr_search_logs(user_id);",
        "CREATE INDEX IF NOT EXISTS idx_sr_search_logs_created ON sr_search_logs(created_at);",
        "CREATE INDEX IF NOT EXISTS idx_ops_daily_reports_date ON ops_daily_reports(report_date);",
        "CREATE INDEX IF NOT EXISTS idx_wms_inventory_sku ON wms_inventory_records(sku_id);",
        "CREATE INDEX IF NOT EXISTS idx_wms_inventory_warehouse ON wms_inventory_records(warehouse_id);",
        "CREATE INDEX IF NOT EXISTS idx_uc_login_logs_user ON uc_login_logs(user_id);",
        "CREATE INDEX IF NOT EXISTS idx_cs_tickets_status ON cs_service_tickets(status);",
        "CREATE INDEX IF NOT EXISTS idx_fin_settlements_shop ON fin_settlements(shop_id);",
        "",
        "-- 完成！",
    ]

    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.write(ddl())
    sys.stdout.write(dml())
