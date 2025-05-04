from fastapi import APIRouter, Depends

from marketbot.context import ServerContext

# The root router
router = APIRouter()


@router.post(
    path='/gold-price',
    summary="Get the latest Gold price",
)
async def slack_command(ctx: ServerContext = Depends()):
    prices = ctx.gold_price.get_latest_gold_prices()
    data = ctx.gold_price.build_slack_message(prices)
    return data
