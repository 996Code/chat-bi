"""
API 公共工具函数 — 消除各 API 模块中重复的 _error() 和 _iso() 定义。

本文件是 API 层的"共享工具箱"，提供两个常用函数：
  - api_error()：构建结构化的错误响应字典
  - iso_format()：将 datetime 格式化为 ISO 8601 字符串

设计原则：
  - 保持简单：每个函数只做一件事
  - 统一格式：所有 API 返回的错误格式一致，方便前端处理
  - 时区处理：统一使用 UTC（Z 后缀），避免前后端时区混乱
"""


def api_error(code: str, message: str) -> dict:
    """构建结构化的错误响应字典。

    用于 HTTPException 的 detail 参数，格式：
        {"code": "ERROR_CODE", "message": "错误描述", "details": None}

    参数:
        code: 错误码，如 "UNAUTHORIZED"、"NOT_FOUND"、"VALIDATION_ERROR"
        message: 错误描述，面向用户的友好提示

    返回:
        dict — 可直接作为 HTTPException(detail=...) 的参数

    示例:
        raise HTTPException(
            status_code=404,
            detail=api_error("NOT_FOUND", "数据源不存在")
        )
    """
    return {"code": code, "message": message, "details": None}


def iso_format(dt) -> str:
    """将 datetime 对象格式化为 ISO 8601 字符串。

    格式：YYYY-MM-DDTHH:MM:SSZ（UTC 时区，Z 后缀表示零时区）

    参数:
        dt: datetime 对象，或 None

    返回:
        str — ISO 8601 格式字符串，如 "2024-03-15T10:30:00Z"
              如果输入为 None，返回空字符串

    为什么用 strftime 而不是 isoformat()？
        - isoformat() 会输出 +00:00 而不是 Z
        - 前端 JavaScript 的 new Date() 对两种格式都支持，
          但 Z 格式更简洁，是 ISO 8601 的 UTC 标准写法
    """
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
