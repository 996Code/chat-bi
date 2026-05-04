from pydantic import BaseModel, field_validator


class QueryRequest(BaseModel):
    question: str
    datasource_id: str

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


class ExplainRequest(BaseModel):
    sql: str
