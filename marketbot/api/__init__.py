from fastapi import APIRouter

from .gold import router as gold
from .slack import router as slack

router = APIRouter()

router.include_router(gold, prefix='/gold')
router.include_router(slack, prefix='/slack')
