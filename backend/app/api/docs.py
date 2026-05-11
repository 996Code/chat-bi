"""文档接口：提供学习指南 Markdown 内容。"""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["docs"])

_GUIDE_PATH = Path(__file__).resolve().parents[2].parent / "doc" / "学习指南-ChatBI代码解读.md"


@router.get("/docs/learning-guide")
async def get_learning_guide():
    if not _GUIDE_PATH.exists():
        return JSONResponse({"error": "Guide file not found"}, status_code=404)
    content = _GUIDE_PATH.read_text(encoding="utf-8")
    return {"content": content}
