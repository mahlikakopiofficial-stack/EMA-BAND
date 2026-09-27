import pytest

import backtest
from core.liquidation import (
    estimated_liquidation_price,
    liquidation_exit_price,
    weighted_average_entry,
)
from core.engine import Engine
from core.models import EntryLot, Position


def test_liquidation_exit_price_uses_remaining_cushion():
    assert liquidation_exit_price(100.0, 70.0, 30.0) == pytest.approx(79.0)


def test_estimated_liquidation_price_and_merged_average():
    lots = [
        backtest.Lot("BTCUSDT", 1, 0, 100.0, 2.0, 200.0, 0.0),
        backtest.Lot("BTCUSDT", 2, 1, 110.0, 1.0, 110.0, 0.0),
    ]
    average = weighted_average_entry(lots)
    liq = estimated_liquidation_price(average, 3.0, 0.005)

    assert average == pytest.approx(103.3333333333)
    assert liq == pytest.approx(69.4055555556)
    assert liquidation_exit_price(average, liq, 30.0) == pytest.approx(79.5838888889)


def test_live_engine_closes_all_lots_at_merged_liquidation_buffer(env):
    settings, store, telegram, adapter = env
    engine = Engine(settings, store, telegram, adapter=adapter)
    engine.lots = {
        "one": EntryLot("one", "BTCUSDT", "LONG", 1.0, 100.0, 1),
        "two": EntryLot("two", "BTCUSDT", "LONG", 2.0, 110.0, 2),
    }
    engine.positions[("BTCUSDT", "LONG")] = Position(
        "BTCUSDT", "LONG", 3.0, 106.6666666667, 1, managed=True, liq_price=70.0
    )

    engine._check_exits("BTCUSDT", 78.0)

    assert engine.lots == {}
    assert store.recent_trades(10)
    assert all(t["reason"] == "liquidation_buffer_exit" for t in store.recent_trades(10))
    assert any("LIQUIDATION BUFFER EXIT" in message for message in telegram.messages)
