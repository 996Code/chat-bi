# SQL 脚本目录

## 结构

```
sql/
├── business/     # 业务数据库初始化（ChatBI 自身）
│   ├── 01_schema.sql        # MySQL
│   └── 01_schema_pg.sql     # PostgreSQL
└── test/         # 测试数据库初始化（电商业务数据）
    ├── 01_mysql_schema.sql  # MySQL 测试库 DDL
    ├── 01_pg_schema.sql     # PostgreSQL 测试库 DDL
    ├── seed_mysql.py        # MySQL 测试数据生成脚本
    └── seed_pg.py           # PostgreSQL 测试数据生成脚本
```

## 业务数据库

ChatBI 自身使用 MySQL 存储业务数据。

```bash
# 初始化
mysql -u root -p < sql/business/01_schema.sql
```

## 测试数据库

包含 500 用户、200 商品、1000 订单等完整电商测试数据。

```bash
# MySQL
cd sql/test && python seed_mysql.py

# PostgreSQL
cd sql/test && python seed_pg.py
```

连接配置在 `backend/tests/test_config.json` 中修改。
