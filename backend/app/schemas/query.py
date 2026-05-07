from pydantic import BaseModel, field_validator


class QueryRequest(BaseModel):
    question: str
    datasource_id: str
    history: list[dict] | None = None  # Previous messages for multi-turn context

    @field_validator("question")
    @classmethod
    def question_not_empty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("问题不能为空")
        if len(stripped) > 1000:
            raise ValueError("问题不能超过1000个字符")
        return stripped


class QueryResponse(BaseModel):
    success: bool
    intent: str | None = None
    sql: str | None = None
    columns: list[str] = []
    rows: list[dict] = []
    row_count: int = 0
    error: str | None = None
    execution_time_ms: int | None = None
    chart_type: str = "none"
    cached: bool = False  # Whether this was a cache hit
    cache_type: str | None = None  # "exact" or "semantic"
    is_slow: bool = False  # Whether total time exceeded slow query threshold


class ExplainRequest(BaseModel):
    sql: str


class AsyncQueryResponse(BaseModel):
    """Response when submitting an async query — returns task_id for polling."""
    task_id: str
    status: str  # pending | running | done | failed | cancelled
    question: str
    datasource_id: str
    created_at: str | None = None


class AsyncQueryStatus(BaseModel):
    """Response when polling for an async query status/result."""
    task_id: str
    status: str
    question: str
    datasource_id: str
    sql: str | None = None
    columns: list[str] = []
    rows: list[dict] = []
    row_count: int = 0
    error: str | None = None
    chart_type: str = "none"
    execution_time_ms: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
