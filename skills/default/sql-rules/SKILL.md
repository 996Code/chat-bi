---
name: sql-business-rules
description: SQL 生成的业务规则
version: 1.0
---

## 状态字段规则
订单状态字段 status 的合法值是英文: paid / cancelled / pending / shipped
不要用中文值 (如"已付款") 查询

## 时间字段规则
所有时间过滤使用 created_at, 不要用 update_time

## GMV 计算规则
GMV = SUM(total_amount) WHERE status IN ('paid', 'shipped')
