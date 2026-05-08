# =============================================================================
# 图表类型推断 — 基于数据特征的规则引擎
# =============================================================================
#
# 【文件用途】
#   根据 SQL 查询结果的列数、行数、数据类型等特征，自动推断最适合的图表类型。
#   不依赖 LLM，纯规则匹配，毫秒级完成。
#
# 【调用链】
#   graph.py (execute_sql 节点之后)
#     → from app.ai.chart_type import infer_chart_type
#     → chart_type = infer_chart_type(columns, rows)
#     → 写入 state.chart_type，供前端 ChartRenderer 组件渲染
#
# 【为什么用规则而不用 LLM？】
#   1. 速度：规则推断 <1ms，LLM 调用 500-2000ms，图表推断是每次查询的必经步骤
#   2. 确定性：相同输入永远返回相同结果，LLM 可能对边界情况摇摆
#   3. 成本：每次查询省一次 LLM 调用，批量场景下成本差异显著
#   4. 可解释性：规则透明可调试，LLM 的"黑盒"推断难以排查
#   5. 数据特征→图表类型的映射本质上是结构化的，规则已足够覆盖
#
# 【推断规则总览】
#   列数 × 行数 × 数据类型 → 图表类型
#   ┌──────────┬──────────────┬──────────────────────────┐
#   │ 列数     │ 条件         │ 图表类型                 │
#   ├──────────┼──────────────┼──────────────────────────┤
#   │ 1列1行   │ -            │ metric (指标卡)          │
#   │ 2列      │ 含时间列     │ line (折线图)            │
#   │ 2列      │ ≤5行+数值列  │ pie (饼图)               │
#   │ 2列      │ 数值列       │ bar (柱状图)             │
#   │ 3列      │ 第3列是数值  │ grouped_bar (分组柱状图) │
#   │ ≥2列     │ ≥10行+2数值列│ scatter (散点图)         │
#   │ 其他     │ -            │ table (表格)             │
#   └──────────┴──────────────┴──────────────────────────┘
# =============================================================================

"""根据查询结果数据特征自动推断合适的图表类型。"""
from typing import Any


# ChartType 使用类型别名而非 Enum，因为前端 ECharts 配置使用字符串字面量，
# 类型别名保持兼容性同时提供类型提示
ChartType = str


def infer_chart_type(columns: list[str], rows: list[dict]) -> ChartType:
    """根据数据特征推断合适的图表类型。

    规则：
    - 1列1行 → metric (指标卡)
    - 2列（时间+数值）→ line
    - 2列（分类+数值）→ bar
    - 2列且行数≤5 → pie
    - 3列（分类+分类+数值）→ grouped_bar
    - 散点特征 → scatter
    - 默认 → table

    参数:
        columns (list[str]) — SQL 结果的列名列表，如 ["month", "revenue"]
        rows (list[dict])   — SQL 结果的数据行，每行是 {列名: 值} 的字典

    返回:
        ChartType (str) — 图表类型字符串，前端 ChartRenderer 据此选择渲染组件

    Python 提示:
        list[str] 是 Python 3.9+ 的内置泛型语法，无需 from __future__ import annotations
    """
    col_count = len(columns)
    row_count = len(rows)

    # 空结果兜底：无数据时返回 table，前端展示空表格而非报错
    if col_count == 0 or row_count == 0:
        return "table"

    # 单值指标：如 SELECT COUNT(*) FROM orders → 只有一个数字，适合指标卡展示
    if col_count == 1 and row_count == 1:
        return "metric"

    if col_count == 2:
        # 取第一行样本值判断数据类型，避免遍历全部行
        # 权衡：样本可能不具代表性（如第一行恰好是异常值），但性能远优于全量扫描
        first_col = _sample_value(rows, columns[0])
        second_col = _sample_value(rows, columns[1])

        # 时间序列检测：任一列看起来像时间就判定为折线图
        # 常见场景：SELECT month, SUM(amount) ... GROUP BY month
        if _looks_like_time(first_col) or _looks_like_time(second_col):
            return "line"

        # 饼图：分类少(≤5) + 数值列，如各状态占比
        # 行数阈值 5 是经验值：超过 5 个扇区可读性急剧下降
        if row_count <= 5 and _is_numeric(second_col):
            return "pie"

        # 柱状图：分类 + 数值，如各城市销售额
        if _is_numeric(second_col):
            return "bar"

    if col_count == 3:
        # 分组柱状图：两个分类维度 + 一个数值
        # 如：城市(分类1) + 产品(分类2) + 销售额(数值)
        third_col = _sample_value(rows, columns[2])
        if _is_numeric(third_col):
            return "grouped_bar"

    if col_count >= 2 and row_count >= 10:
        # 散点图：多行 + 至少两个数值列，暗示连续变量间的相关性
        # 行数阈值 10：散点图数据点太少没有分析价值
        num_cols = [c for c in columns if _is_numeric_column(c, rows)]
        if len(num_cols) >= 2:
            return "scatter"

    # 兜底：无法匹配任何图表规则时返回表格，最安全的数据展示方式
    return "table"


def _sample_value(rows: list[dict], col: str) -> Any:
    """从第一行提取指定列的样本值，用于快速判断列的数据类型。

    参数:
        rows — 数据行列表
        col  — 列名

    返回:
        Any — 该列的样本值，空行或列不存在时返回 None
    """
    if not rows or col not in rows[0]:
        return None
    return rows[0].get(col)


def _is_numeric(value: Any) -> bool:
    """判断单个值是否为数值类型。

    支持类型:
        - int / float → 直接判定
        - str → 尝试转 float，兼容 "1,234.56" 等千分位格式

    参数:
        value — 待判断的值

    返回:
        bool — True 表示数值类型

    Python 提示:
        isinstance(value, (int, float)) 用元组同时匹配多种类型，
        比 isinstance(value, int) or isinstance(value, float) 更简洁
    """
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            # 去除千分位逗号：如 "1,234.56" → "1234.56"
            float(value.replace(",", ""))
            return True
        except ValueError:
            return False
    return False


def _looks_like_time(value: Any) -> bool:
    """启发式判断值是否为时间/日期类型。

    通过字符串包含的关键词判断，而非正则或 datetime 解析。
    原因：SQL 查询结果的时间列可能是 DATE/DATETIME/TIMESTAMP/VARCHAR，
         格式不统一，关键词匹配覆盖面更广且性能更好。

    参数:
        value — 待判断的值

    返回:
        bool — True 表示可能为时间类型

    局限性:
        - "20" 会误匹配（如产品编号 "2024A"），但实际场景中时间列很少单独出现 "20"
        - 中文关键词（年/月/日）覆盖中文场景，英文关键词覆盖英文场景
    """
    if value is None:
        return False
    s = str(value).lower()
    # 中英文时间关键词 + 月份前缀 + 世纪前缀 "20"（匹配 2024、2025 等）
    time_indicators = ["年", "月", "日", "quarter", "week", "date", "time", "-01", "-02", "-03", "-04", "-05", "-06", "-07", "-08", "-09", "-10", "-11", "-12", "20"]
    return any(ind in s for ind in time_indicators)


def _is_numeric_column(col: str, rows: list[dict]) -> bool:
    """判断整列是否为数值列（采样前3行，超过50%为数值即判定）。

    参数:
        col  — 列名
        rows — 数据行列表

    返回:
        bool — True 表示该列是数值列

    设计:
        - 只检查前3行：性能与准确性的权衡，3行足以识别列的基本类型
        - 50% 阈值：允许少量 NULL 或异常值，不会因一行脏数据误判
    """
    if not rows:
        return False
    numeric_count = 0
    checked = 0
    for row in rows[:3]:
        val = row.get(col)
        if val is not None:
            checked += 1
            if _is_numeric(val):
                numeric_count += 1
    return checked > 0 and numeric_count / checked > 0.5
