"""敏感数据脱敏：对查询结果中的手机号、身份证、邮箱等做掩码处理。"""
import re
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Masking patterns: (regex, replacement function)
MASKING_RULES = [
    # Chinese phone: 13812345678 -> 138****5678
    (re.compile(r"^1[3-9]\d{9}$"), lambda m: m.group()[:3] + "****" + m.group()[-4:]),
    # Chinese ID: 18 digits -> **************1234
    (re.compile(r"^\d{17}[\dXx]$"), lambda m: "***************" + m.group()[-4:]),
    # Email: john@example.com -> j***n@example.com
    (re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"), lambda m: m.group()[0] + "***@" + m.group().split("@")[1]),
]

# Column name patterns that suggest sensitive data
SENSITIVE_COLUMN_PATTERNS = [
    "phone", "mobile", "手机号", "电话", "联系电话",
    "id_card", "idcard", "身份证", "证件号",
    "email", "邮箱", "邮件",
    "bank_card", "bankcard", "银行卡", "卡号",
    "password", "密码", "secret", "token",
]


def _is_sensitive_column(col_name: str) -> bool:
    col_lower = col_name.lower()
    return any(kw in col_lower for kw in SENSITIVE_COLUMN_PATTERNS)


def _mask_value(value: str) -> str:
    """Apply masking rules to a string value."""
    for pattern, repl in MASKING_RULES:
        if pattern.match(value):
            return repl(value)
    return value


def mask_sensitive_data(columns: list[str], rows: list[dict]) -> tuple[list[str], list[dict]]:
    """Mask sensitive columns in query results.

    Returns (columns, masked_rows). Only masks columns whose names
    match known sensitive patterns.
    """
    if not settings.app_env == "production":
        # Only mask in production by default
        # But we still mask for all envs if column name is highly sensitive
        pass  # Allow masking in all envs for safety

    # Identify sensitive columns
    sensitive_cols = {col for col in columns if _is_sensitive_column(col)}
    if not sensitive_cols:
        return columns, rows

    masked_rows = []
    for row in rows:
        masked_row = {}
        for col, value in row.items():
            if col in sensitive_cols and isinstance(value, str) and value:
                masked_row[col] = _mask_value(value)
            else:
                masked_row[col] = value
        masked_rows.append(masked_row)

    if sensitive_cols:
        logger.info("Masked sensitive columns: %s", sensitive_cols)

    return columns, masked_rows
