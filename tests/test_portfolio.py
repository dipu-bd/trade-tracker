import pytest

from marketbot.db import AssetClass, ExitReason, Instrument, PositionStatus


def add_instrument(session, symbol='AAA', asset_class=AssetClass.STOCK):
    instrument = Instrument(
        symbol=symbol, asset_class=asset_class, sector='Technology'
    )
    session.add(instrument)
    session.flush()
    return instrument


def open_test_position(ctx, session, portfolio, instrument, qty=10.0, price=100.0):
    return ctx.portfolios.buy(
        session, portfolio, instrument,
        qty=qty, price=price, stop_price=price * 0.9,
        target_price=price * 1.3, r_value=price * 0.1,
        atr=price * 0.05, max_hold_days=15, entry_score=70.0,
    )


def test_create_rejects_non_positive_capital(ctx):
    with ctx.db.session() as session:
        with pytest.raises(ValueError):
            ctx.portfolios.create(session, name='bad', initial_capital=0)


def test_create_seeds_cash_from_capital(ctx):
    with ctx.db.session() as session:
        portfolio = ctx.portfolios.create(
            session, name='Book', initial_capital=25_000.0
        )
        assert portfolio.cash == 25_000.0
        assert portfolio.initial_capital == 25_000.0


def test_buy_applies_slippage_and_deducts_cash(ctx, portfolio):
    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        book.slippage_bps = 10.0
        book.commission_bps = 0.0
        instrument = add_instrument(session)

        position = open_test_position(ctx, session, book, instrument)

        assert position.avg_entry == pytest.approx(100.1)
        assert book.cash == pytest.approx(100_000 - 1001.0)


def test_buy_charges_commission_on_crypto(ctx, portfolio):
    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        book.slippage_bps = 0.0
        book.crypto_commission_bps = 10.0
        instrument = add_instrument(session, 'BTC_USD', AssetClass.CRYPTO)

        open_test_position(ctx, session, book, instrument, qty=1.0, price=1000.0)

        # 1,000 notional plus 10bps of commission.
        assert book.cash == pytest.approx(100_000 - 1001.0)


def test_buy_is_refused_when_cash_is_short(ctx, portfolio):
    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        book.cash = 50.0
        instrument = add_instrument(session)

        position = open_test_position(ctx, session, book, instrument)
        assert position is None
        assert book.cash == 50.0


def test_sell_returns_cash_and_books_the_pnl(ctx, portfolio):
    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        book.slippage_bps = 0.0
        book.commission_bps = 0.0
        instrument = add_instrument(session)
        position = open_test_position(ctx, session, book, instrument)

        ctx.portfolios.sell(
            session, book, position, price=120.0, reason=ExitReason.TAKE_PROFIT
        )

        assert position.status == PositionStatus.CLOSED
        assert position.exit_reason == ExitReason.TAKE_PROFIT
        assert position.realized_pnl == pytest.approx(200.0)
        assert book.cash == pytest.approx(100_000 + 200.0)


def test_round_trip_costs_come_out_of_the_pnl(ctx, portfolio):
    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        book.slippage_bps = 0.0
        book.commission_bps = 10.0
        instrument = add_instrument(session)
        position = open_test_position(ctx, session, book, instrument)

        ctx.portfolios.sell(
            session, book, position, price=100.0, reason=ExitReason.SIGNAL_EXIT
        )

        # Flat price, so the whole loss is the two commissions: 10bps of 1,000
        # on the way in and on the way out.
        assert position.realized_pnl == pytest.approx(-2.0)


def test_selling_a_closed_position_is_a_no_op(ctx, portfolio):
    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        instrument = add_instrument(session)
        position = open_test_position(ctx, session, book, instrument)
        ctx.portfolios.sell(session, book, position, 110.0, ExitReason.MANUAL)

        assert ctx.portfolios.sell(
            session, book, position, 110.0, ExitReason.MANUAL
        ) is None


def test_equity_reconciles_cash_plus_market_value(ctx, portfolio):
    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        book.slippage_bps = 0.0
        book.commission_bps = 0.0
        instrument = add_instrument(session)
        open_test_position(ctx, session, book, instrument)

        positions = ctx.portfolios.open_positions(session, book)
        prices = {'AAA': 110.0}

        assert ctx.portfolios.positions_value(positions, prices) == pytest.approx(1100)
        assert ctx.portfolios.equity(book, positions, prices) == pytest.approx(
            book.cash + 1100
        )


def test_sleeve_exposure_separates_crypto_from_equities(ctx, portfolio):
    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        book.slippage_bps = 0.0
        book.commission_bps = 0.0
        book.crypto_commission_bps = 0.0
        stock = add_instrument(session, 'AAA')
        coin = add_instrument(session, 'BTC_USD', AssetClass.CRYPTO)
        open_test_position(ctx, session, book, stock, qty=10, price=100)
        open_test_position(ctx, session, book, coin, qty=2, price=500)

        positions = ctx.portfolios.open_positions(session, book)
        prices = {'AAA': 100.0, 'BTC_USD': 500.0}

        assert ctx.portfolios.sleeve_exposure(
            positions, prices, (AssetClass.CRYPTO,)
        ) == pytest.approx(1000.0)
        assert ctx.portfolios.sleeve_exposure(
            positions, prices, AssetClass.EQUITY_CLASSES
        ) == pytest.approx(1000.0)


def test_snapshot_records_equity_and_drawdown(ctx, portfolio):
    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        instrument = add_instrument(session)
        open_test_position(ctx, session, book, instrument)

        positions = ctx.portfolios.open_positions(session, book)
        snapshot = ctx.portfolios.snapshot(session, book, {'AAA': 90.0}, positions)

        assert snapshot.open_positions == 1
        assert snapshot.equity == pytest.approx(book.cash + 900.0)
        assert snapshot.drawdown_pct > 0


def test_liquidate_closes_everything(ctx, portfolio):
    with ctx.db.session() as session:
        book = ctx.portfolios.get(session, portfolio)
        for symbol in ('AAA', 'BBB'):
            instrument = add_instrument(session, symbol)
            open_test_position(ctx, session, book, instrument)

        closed = ctx.portfolios.liquidate(
            session, book, {'AAA': 105.0, 'BBB': 95.0}, ExitReason.RISK_HALT
        )

        assert len(closed) == 2
        assert ctx.portfolios.open_positions(session, book) == []
