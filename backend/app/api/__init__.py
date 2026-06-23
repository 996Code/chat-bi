"""
ChatBI v2 — API Router
"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/ping")
async def ping():
    return {"message": "pong"}
