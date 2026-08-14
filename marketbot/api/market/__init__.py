from typing import Optional

from fastapi import APIRouter, Depends, Query

from marketbot.context import ServerContext
from marketbot.db import Sleeve
from marketbot.security import verify_access_token
from marketbot.services import scanner, strategy

router = APIRouter(dependencies=[Depends(verify_access_token)])


@router.get('/regime', summary='Current market regime from the benchmark')
def get_regime(ctx: ServerContext = Depends()):
    with ctx.db.session() as session:
        benchmark = ctx.engine.benchmark_instrument(session)
        bars = ctx.market_data.get_bars(session, benchmark) if benchmark else []
        return {
            'benchmark': strategy.REGIME_BENCHMARK,
            'regime': strategy.detect_regime(bars),
            'bars_available': len(bars),
        }


@router.get('/candidates', summary='Ranked candidates without trading anything')
def get_candidates(
    sleeve: str = Query(default=Sleeve.ALL, pattern='^(all|equity|crypto)$'),
    limit: int = Query(default=25, ge=1, le=100),
    refresh: bool = Query(
        default=False, description='Fetch fresh bars before scoring'
    ),
    ctx: ServerContext = Depends(),
):
    with ctx.db.session() as session:
        instruments = ctx.market_data.sync_universe(session, sleeve)
        if refresh:
            ctx.market_data.refresh_bars(session, instruments)
        quotes = ctx.market_data.get_quotes(session, instruments)

        candidates = []
        for instrument in instruments:
            bars = ctx.market_data.get_bars(session, instrument)
            candidate = scanner.build_candidate(
                instrument, bars, quotes.get(instrument.symbol)
            )
            if candidate is not None:
                candidates.append(candidate)

        ranked = scanner.rank(candidates)[:limit]
        return {
            'sleeve': sleeve,
            'universe_size': len(instruments),
            'scored': len(candidates),
            'candidates': [c.brief() for c in ranked],
        }
