from fastapi import APIRouter

from .slack import router as slack

router = APIRouter()

router.include_router(
    slack,
    prefix='/slack',
)
