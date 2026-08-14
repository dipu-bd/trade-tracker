from fastapi import APIRouter

from .gold import router as gold
from .market import router as market
from .portfolio import router as portfolio
from .slack import router as slack

router = APIRouter()

router.include_router(gold, prefix='/gold')
router.include_router(slack, prefix='/slack')
router.include_router(portfolio, prefix='/portfolio', tags=['portfolio'])
router.include_router(market, prefix='/market', tags=['market'])
