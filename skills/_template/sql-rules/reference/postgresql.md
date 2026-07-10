## PostgreSQL 方言规则

- 时间范围使用 `BETWEEN` 语法（如 `created_at BETWEEN '2024-01-01' AND '2024-12-31'`）
- 字符串比较区分大小写，需要时用 `LOWER()` 或 `ILIKE`
- `LIMIT` 直接使用，不需要 `TOP`
- 日期函数使用 `DATE_TRUNC()` 进行时间聚合（如 `DATE_TRUNC('month', created_at)`）
- JSON 字段查询使用 `->` 和 `->>` 操作符
