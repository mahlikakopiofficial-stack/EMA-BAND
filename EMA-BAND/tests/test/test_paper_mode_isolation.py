from unittest.mock import Mock

from core.engine import Engine
from core.models import Trade
from conftest import DummyStream


def make_signal():
    return type(
        "Signal",
        (),
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "candle_start": 900000,
            "close": 100.0,
            "reason": "paper-route-test",
        },
    )()


def test_paper_snapshot_uses_virtual_capital_not_private_wallet_calls(env):
    settings, store, telegram, adapter = env
    engine = Engine(settings, store, telegram, adapter=adapter)
    adapter.get_balance = Mock(side_effect=AssertionError("private wallet called in paper mode"))
    adapter.get_positions = Mock(side_effect=AssertionError("private positions called in paper mode"))

    equity, available, unrealized = engine.account_snapshot()

    assert equity == 1000.0
    assert available == 1000.0
    assert unrealized == 0.0
    adapter.get_balance.assert_not_called()
    adapter.get_positions.assert_not_called()
    assert adapter.orders == []


def test_paper_entry_routes_signal_to_virtual_lot_without_exchange_order(env):
    settings, store, telegram, adapter = env
    engine = Engine(settings, store, telegram, adapter=adapter)
    engine.ws_market = DummyStream()
    adapter.get_balance = Mock(side_effect=AssertionError("private wallet called in paper mode"))
    adapter.get_positions = Mock(side_effect=AssertionError("private positions called in paper mode"))
    adapter.size_from_balance = Mock(return_value=1.0)

    accepted = engine.open_position(make_signal())

    assert accepted is True
    adapter.size_from_balance.assert_called_once_with("BTCUSDT", 1000.0, 10.0, 100.0)
    adapter.get_balance.assert_not_called()
    adapter.get_positions.assert_not_called()
    assert adapter.orders == []
    assert store.lot_count("BTCUSDT", "LONG") == 1
    assert any("PAPER ENTRY" in message for message in telegram.messages)


def test_paper_reconcile_never_queries_private_order_or_position_endpoints(env):
    settings, store, telegram, adapter = env
    engine = Engine(settings, store, telegram, adapter=adapter)
    adapter.get_positions = Mock(side_effect=AssertionError("private positions called in paper mode"))
    adapter.get_order_history = Mock(side_effect=AssertionError("private order history called in paper mode"))

    engine.reconcile()

    adapter.get_positions.assert_not_called()
    adapter.get_order_history.assert_not_called()


def test_paper_start_can_run_without_private_endpoints(env):
    settings, store, telegram, adapter = env
    settings.validate = lambda: []
    engine = Engine(settings, store, telegram, adapter=adapter)
    engine.ws_market = DummyStream()
    adapter.get_positions = Mock(side_effect=AssertionError("private positions called in paper mode"))
    adapter.get_order_history = Mock(side_effect=AssertionError("private order history called in paper mode"))

    engine.start()

    assert engine.running is True
    adapter.get_positions.assert_not_called()
    adapter.get_order_history.assert_not_called()
    assert adapter.orders == []


def test_virtual_equity_includes_persisted_realized_pnl(env):
    settings, store, telegram, adapter = env
    store.trade(
        Trade(
            symbol="BTCUSDT",
            side="LONG",
            qty=1.0,
            entry_price=100.0,
            exit_price=112.0,
            pnl=11.0,
            fee=1.0,
            opened_at_ms=1,
            closed_at_ms=2,
            cycle_id="paper-cycle",
            reason="test",
        )
    )
    engine = Engine(settings, store, telegram, adapter=adapter)

    equity, available, unrealized = engine.account_snapshot()

    assert equity == 1011.0
    assert available == 1011.0
    assert unrealized == 0.0
    assert store.total_realized_pnl() == 11.0
    assert adapter.orders == []
