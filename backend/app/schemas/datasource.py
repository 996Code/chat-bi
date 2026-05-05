from pydantic import BaseModel, field_validator


SUPPORTED_DB_TYPES = {"mysql", "postgresql"}

class DataSourceCreate(BaseModel):
    name: str
    type: str = "mysql"
    host: str
    port: int = 3306
    database_name: str
    username: str
    password: str
    extra_params: dict = {}

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("数据源名称不能为空")
        if len(stripped) > 100:
            raise ValueError("数据源名称不能超过100个字符")
        return stripped

    @field_validator("type")
    @classmethod
    def type_is_supported(cls, v: str) -> str:
        if v not in SUPPORTED_DB_TYPES:
            raise ValueError(f"仅支持以下数据库类型: {', '.join(sorted(SUPPORTED_DB_TYPES))}")
        return v


class DataSourceUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    port: int | None = None
    database_name: str | None = None
    username: str | None = None
    password: str | None = None
    is_active: bool | None = None
    extra_params: dict | None = None


class DataSourceResponse(BaseModel):
    id: str
    name: str
    type: str
    host: str
    port: int
    database_name: str
    status: str
    last_health_check: str | None = None
    health_check_error: str | None = None
