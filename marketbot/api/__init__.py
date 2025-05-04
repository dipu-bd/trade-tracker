from fastapi import APIRouter, Depends

from ..context import verify_token
from .slack import router as slack

router = APIRouter()

router.include_router(
    slack,
    prefix='/slack',
    dependencies=[Depends(verify_token)],
)
