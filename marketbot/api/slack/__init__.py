from fastapi import APIRouter, Depends

from marketbot.context import ServerContext

# The root router
router = APIRouter()


@router.post(
    path="/gold-price",
    summary="Send slack alert for the current Gold price",
)
def send_gold_price_alert(ctx: ServerContext = Depends()):
    return ctx.gold_price.send_latest_prices()
