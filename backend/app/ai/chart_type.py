"""根据查询结果数据特征自动推断合适的图表类型。"""
from typing import Any


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
    """
    col_count = len(columns)
    row_count = len(rows)

    if col_count == 0 or row_count == 0:
        return "table"

    # Single metric → metric card
    if col_count == 1 and row_count == 1:
        return "metric"

    if col_count == 2:
        first_col = _sample_value(rows, columns[0])
        second_col = _sample_value(rows, columns[1])

        # Time series → line chart
        if _looks_like_time(first_col) or _looks_like_time(second_col):
            return "line"

        # Few categories → pie chart
        if row_count <= 5 and _is_numeric(second_col):
            return "pie"

        # Default 2 columns → bar chart
        if _is_numeric(second_col):
            return "bar"

    if col_count == 3:
        # category + category + value → grouped bar
        third_col = _sample_value(rows, columns[2])
        if _is_numeric(third_col):
            return "grouped_bar"

    if col_count >= 2 and row_count >= 10:
        # Many rows with 2+ numeric columns → scatter
        num_cols = [c for c in columns if _is_numeric_column(c, rows)]
        if len(num_cols) >= 2:
            return "scatter"

    return "table"


def _sample_value(rows: list[dict], col: str) -> Any:
    if not rows or col not in rows[0]:
        return None
    return rows[0].get(col)


def _is_numeric(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value.replace(",", ""))
            return True
        except ValueError:
            return False
    return False


def _looks_like_time(value: Any) -> bool:
    if value is None:
        return False
    s = str(value).lower()
    time_indicators = ["年", "月", "日", "quarter", "week", "date", "time", "-01", "-02", "-03", "-04", "-05", "-06", "-07", "-08", "-09", "-10", "-11", "-12", "20"]
    return any(ind in s for ind in time_indicators)


def _is_numeric_column(col: str, rows: list[dict]) -> bool:
    if not rows:
        return False
    # Check first 3 rows
    numeric_count = 0
    checked = 0
    for row in rows[:3]:
        val = row.get(col)
        if val is not None:
            checked += 1
            if _is_numeric(val):
                numeric_count += 1
    return checked > 0 and numeric_count / checked > 0.5
