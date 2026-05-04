"""时间表达式解析：中文 → SQL WHERE 范围。"""
import re
from datetime import datetime, timedelta
from typing import Any

logger = __import__("logging").getLogger(__name__)


def parse_time_expression(text: str) -> dict[str, Any] | None:
    """解析中文时间表达 → {"field": "created_at", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}。

    支持：上个月、本月、近N天、近N月、近N周、Q1-Q4、2024年3月、2024年Q2、今年、去年
    """
    now = datetime.now()

    # 相对时间
    patterns = [
        (r'上个月', _last_month),
        (r'上月', _last_month),
        (r'这个月|本月|当月', _this_month),
        (r'上周|上个星期', _last_week),
        (r'近(\d+)天|过去(\d+)天|最近(\d+)天', _last_n_days),
        (r'近(\d+)周|过去(\d+)周|最近(\d+)周', _last_n_weeks),
        (r'近(\d+)个?月|过去(\d+)个?月', _last_n_months),
        (r'近(\d+)年|过去(\d+)年', _last_n_years),
        (r'今年|本年|今年内', _this_year),
        (r'去年|上年', _last_year),
    ]

    for pattern, handler in patterns:
        m = re.search(pattern, text)
        if m:
            start, end = handler(m, now)
            if start and end:
                return {
                    "field": _detect_time_field(text),
                    "start": start.strftime("%Y-%m-%d"),
                    "end": end.strftime("%Y-%m-%d 23:59:59"),
                    "relative": m.group(0),
                }

    # 绝对时间: 2024年3月、2024-03、2024年Q2
    m = re.search(r'(\d{4})年(\d{1,2})月?', text)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        start = datetime(year, month, 1)
        if month == 12:
            end = datetime(year + 1, 1, 1) - timedelta(seconds=1)
        else:
            end = datetime(year, month + 1, 1) - timedelta(seconds=1)
        return {
            "field": _detect_time_field(text),
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d 23:59:59"),
        }

    m = re.search(r'(\d{4})[年\-]Q([1-4])', text)
    if m:
        year, q = int(m.group(1)), int(m.group(2))
        start = datetime(year, (q - 1) * 3 + 1, 1)
        if q == 4:
            end = datetime(year + 1, 1, 1) - timedelta(seconds=1)
        else:
            end = datetime(year, q * 3 + 1, 1) - timedelta(seconds=1)
        return {
            "field": _detect_time_field(text),
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d 23:59:59"),
        }

    return None


def _detect_time_field(text: str) -> str:
    """根据上下文推测时间字段，默认 created_at。"""
    if "支付" in text or "付款" in text:
        return "paid_at"
    if "发货" in text or "配送" in text or "快递" in text:
        return "shipped_at"
    if "趋势" in text or "月" in text or "日" in text or "年" in text:
        return "created_at"
    return "created_at"


def _last_month(m, now: datetime):
    first = now.replace(day=1)
    last_month_end = first - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    return last_month_start, last_month_end


def _this_month(m, now: datetime):
    start = now.replace(day=1)
    return start, now


def _last_week(m, now: datetime):
    today = now.weekday()
    start = (now - timedelta(days=today + 7)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7) - timedelta(seconds=1)
    return start, end


def _last_n_days(m, now: datetime):
    n = int(m.group(1) or m.group(2) or m.group(3))
    end = now
    start = now - timedelta(days=n)
    return start, end


def _last_n_weeks(m, now: datetime):
    n = int(m.group(1) or m.group(2) or m.group(3))
    end = now
    start = now - timedelta(weeks=n)
    return start, end


def _last_n_months(m, now: datetime):
    n = int(m.group(1) or m.group(2))
    end = now
    year = now.year - (now.month - n) // 12
    month = (now.month - n) % 12 + 1
    # Avoid month-end overflow (e.g., Mar 31 - 1 month → Feb 28)
    import calendar
    max_day = calendar.monthrange(year, month)[1]
    day = min(now.day, max_day)
    start = datetime(year, month, day)
    return start, end


def _last_n_years(m, now: datetime):
    n = int(m.group(1) or m.group(2))
    end = now
    start = datetime(now.year - n, now.month, now.day)
    return start, end


def _this_year(m, now: datetime):
    return datetime(now.year, 1, 1), now


def _last_year(m, now: datetime):
    return datetime(now.year - 1, 1, 1), datetime(now.year, 1, 1) - timedelta(seconds=1)
