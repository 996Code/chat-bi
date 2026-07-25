## MySQL 方言规则

- 时间范围使用 `BETWEEN` 语法（如 `created_at BETWEEN '2024-01-01' AND '2024-12-31'`）
- 字符串比较默认不区分大小写，需要区分时用 `BINARY`
- `LIMIT` 直接使用，不支持 `OFFSET` 独立语法（使用 `LIMIT offset, count`）
- 日期函数使用 `DATE_FORMAT()` 进行时间格式化（如 `DATE_FORMAT(created_at, '%Y-%m')`）
- `GROUP_CONCAT` 替代 PostgreSQL 的 `STRING_AGG`
- 自动递增用 `AUTO_INCREMENT`，不要用 `SERIAL`
