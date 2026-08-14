import pytest

from marketbot.advisors import apply_verdicts
from marketbot.advisors.base import AdvisorResult, Verdict, parse_verdicts
from marketbot.db import AssetClass, ExitReason
from marketbot.services.strategy import ActionPlan, ProposedEntry, ProposedExit
from tests.test_strategy import make_candidate, make_position


def make_entry(symbol='AAA', qty=100.0, price=100.0) -> ProposedEntry:
    return ProposedEntry(
        candidate=make_candidate(symbol=symbol),
        qty=qty, entry_price=price, stop_price=price * 0.9,
        target_price=price * 1.3, r_value=price * 0.1,
        atr=price * 0.05, max_hold_days=15,
    )


def make_result(*verdicts) -> AdvisorResult:
    return AdvisorResult(provider='fake', model='m', verdicts=list(verdicts))


def make_holdings(*positions):
    return {
        p.instrument.symbol.upper(): (p, p.avg_entry * 1.05, 50.0)
        for p in positions
    }


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def test_parses_a_well_formed_payload():
    verdicts = parse_verdicts(
        '{"verdicts":[{"symbol":"aaa","action":"BUY","verdict":"reject",'
        '"confidence":0.8,"reason":"chased"}]}'
    )
    assert len(verdicts) == 1
    assert verdicts[0].symbol == 'AAA'
    assert verdicts[0].rejects_entry


def test_parses_a_fenced_payload():
    verdicts = parse_verdicts(
        '```json\n{"verdicts":[{"symbol":"AAA","action":"BUY",'
        '"verdict":"approve","confidence":1,"reason":"ok"}]}\n```'
    )
    assert len(verdicts) == 1


def test_parses_json_embedded_in_prose():
    verdicts = parse_verdicts(
        'Here you go: {"verdicts":[{"symbol":"AAA","action":"HOLD",'
        '"verdict":"hold","confidence":0.5,"reason":"fine"}]} — hope that helps'
    )
    assert len(verdicts) == 1


def test_malformed_payloads_yield_nothing():
    assert parse_verdicts('not json at all') == []
    assert parse_verdicts('{"verdicts": "nope"}') == []
    assert parse_verdicts(None) == []


def test_a_verdict_outside_the_allowed_set_is_dropped():
    verdicts = parse_verdicts(
        '{"verdicts":[{"symbol":"AAA","action":"BUY","verdict":"double_down",'
        '"confidence":1,"reason":"x"}]}'
    )
    assert verdicts == []


def test_a_hold_cannot_borrow_a_buy_verdict():
    verdicts = parse_verdicts(
        '{"verdicts":[{"symbol":"AAA","action":"HOLD","verdict":"reduce",'
        '"confidence":1,"reason":"x"}]}'
    )
    assert verdicts == []


def test_confidence_is_clamped_to_the_unit_range():
    verdicts = parse_verdicts(
        '{"verdicts":[{"symbol":"AAA","action":"BUY","verdict":"approve",'
        '"confidence":7,"reason":"x"}]}'
    )
    assert verdicts[0].confidence == 1.0


# --------------------------------------------------------------------------- #
# Applying verdicts
# --------------------------------------------------------------------------- #

def test_reject_removes_the_proposed_entry():
    plan = ActionPlan(entries=[make_entry('AAA'), make_entry('BBB')])
    result = make_result(Verdict('AAA', 'BUY', 'reject', 'chased'))

    rows = apply_verdicts(plan, result, 'veto', {})

    assert [e.candidate.symbol for e in plan.entries] == ['BBB']
    assert rows[0]['applied'] is True


def test_reduce_halves_the_size_but_keeps_the_trade():
    plan = ActionPlan(entries=[make_entry('AAA', qty=100.0)])
    result = make_result(Verdict('AAA', 'BUY', 'reduce', 'thin conviction'))

    apply_verdicts(plan, result, 'veto', {})

    assert plan.entries[0].qty == pytest.approx(50.0)


def test_reduce_drops_a_trade_that_cannot_be_halved():
    plan = ActionPlan(entries=[make_entry('AAA', qty=1.0)])
    result = make_result(Verdict('AAA', 'BUY', 'reduce', 'thin'))

    apply_verdicts(plan, result, 'veto', {})

    assert plan.entries == []


def test_approve_leaves_the_entry_untouched():
    plan = ActionPlan(entries=[make_entry('AAA', qty=100.0)])
    result = make_result(Verdict('AAA', 'BUY', 'approve', 'clean setup'))

    apply_verdicts(plan, result, 'veto', {})

    assert plan.entries[0].qty == pytest.approx(100.0)
    assert plan.entries[0].advisor_note == 'clean setup'


def test_the_advisor_cannot_invent_an_entry():
    plan = ActionPlan(entries=[make_entry('AAA')])
    result = make_result(Verdict('ZZZ', 'BUY', 'approve', 'I like this one'))

    rows = apply_verdicts(plan, result, 'veto', {})

    assert [e.candidate.symbol for e in plan.entries] == ['AAA']
    assert rows == []


def test_the_advisor_can_force_an_exit_on_a_holding():
    position = make_position(symbol='HELD')
    plan = ActionPlan()
    result = make_result(Verdict('HELD', 'HOLD', 'force_exit', 'thesis broke'))

    apply_verdicts(plan, result, 'veto', make_holdings(position))

    assert len(plan.exits) == 1
    assert plan.exits[0].reason == ExitReason.ADVISOR_EXIT
    assert plan.exits[0].position is position


def test_the_advisor_cannot_cancel_a_protective_exit():
    position = make_position(symbol='HELD')
    plan = ActionPlan(exits=[
        ProposedExit(position=position, price=90.0, reason=ExitReason.STOP_LOSS)
    ])
    result = make_result(Verdict('HELD', 'HOLD', 'hold', 'give it room'))

    apply_verdicts(plan, result, 'veto', make_holdings(position))

    assert len(plan.exits) == 1
    assert plan.exits[0].reason == ExitReason.STOP_LOSS


def test_annotate_mode_records_advice_without_changing_anything():
    position = make_position(symbol='HELD')
    plan = ActionPlan(entries=[make_entry('AAA', qty=100.0)])
    result = make_result(
        Verdict('AAA', 'BUY', 'reject', 'chased'),
        Verdict('HELD', 'HOLD', 'force_exit', 'broken'),
    )

    rows = apply_verdicts(plan, result, 'annotate', make_holdings(position))

    assert len(plan.entries) == 1
    assert plan.entries[0].qty == pytest.approx(100.0)
    assert plan.exits == []
    assert all(row['applied'] is False for row in rows)


def test_a_failed_call_changes_nothing():
    plan = ActionPlan(entries=[make_entry('AAA', qty=100.0)])
    result = AdvisorResult(provider='fake', model='m', error='timeout')

    rows = apply_verdicts(plan, result, 'veto', {})

    assert rows == []
    assert plan.entries[0].qty == pytest.approx(100.0)
