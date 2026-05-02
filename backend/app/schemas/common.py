from pydantic import BaseModel


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict | None = None
