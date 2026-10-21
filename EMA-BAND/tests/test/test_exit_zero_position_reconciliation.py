from types import SimpleNamespace

import pytest

from core.bybit import BybitAdapter
from core.engine import Engine
from core.models import EntryLot, PendingOrder, Position


def test_bybit_110017_is_not_retried():
    adapter = object.__new__(BybitAdapter)
    calls = {"n": 0}

    def reject():
        calls["n"] += 1
        raise RuntimeError(
            "current position is zero, cannot fix reduce-only order qty (ErrCode: 110017)"
        )

    with pytest.raises(RuntimeError, match="110017"):
        adapter._with_retry(reject, max_retries=3)

    assert calls["n"] == 1


def test_110017_drops_pending_and_persists_reconciliation_hold(env):
    settings, store, telegram, adapter = env
    settings.enable_live_trading = True
    engine = Engine(settings, store, telegram, adapter=adapter)

    lot = EntryLot(
        lot_id="LOT-AR-ZERO",
        symbol="BTCUSDT",
        side="LONG",
        qty=1.4,
        entry_price=100.0,
        entry_time_ms=1,
        entry_fee=0.01,
        exit_armed=True,
    )
    engine.lots[lot.lot_id] = lot
    engine.positions[("BTCUSDT", "LONG")] = Position(
        "BTCUSDT", "LONG", 1.4, 100.0, 1, managed=True
    )
    store.save_lot(lot)
    store.save_position(engine.positions[("BTCUSDT", "LONG")])

    calls = {"n": 0}

    def reject_close(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError(
            "current position is zero, cannot fix reduce-only order qty (ErrCode: 110017)"
        )

    adapter.close_market = reject_close
    adapter.positions = []

    engine.request_close_lot(lot, 110.0, "strategy_target_then_profitable")

    assert calls["n"] == 1
    assert not engine.pending
    assert lot.exit_reconciliation_required is True
    assert store.load_lots("OPEN")[0].exit_reconciliation_required is True
    assert ("BTCUSDT", "LONG") not in engine.positions
    assert any(x["kind"] == "EXIT_RECONCILIATION_REQUIRED" for x in store.recent_alerts())
    assert any("EXIT RECONCILIATION REQUIRED" in msg for msg in telegram.messages)

    # Both strategy exits and fresh entries stay blocked until the mismatch is reviewed.
    engine.request_close_lot(lot, 110.0, "strategy_target_then_profitable")
    assert engine.open_position(SimpleNamespace(symbol="BTCUSDT")) is False
    assert calls["n"] == 1


def test_zero_position_private_update_flags_stale_local_lot(env):
    settings, store, telegram, adapter = env
    settings.enable_live_trading = True
    engine = Engine(settings, store, telegram, adapter=adapter)
    lot = EntryLot(
        lot_id="LOT-WS-ZERO",
        symbol="BTCUSDT",
        side="LONG",
        qty=1.0,
        entry_price=100.0,
        entry_time_ms=1,
    )
    engine.lots[lot.lot_id] = lot
    engine.positions[("BTCUSDT", "LONG")] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1, managed=True
    )
    store.save_lot(lot)
    store.save_position(engine.positions[("BTCUSDT", "LONG")])

    engine.on_position({"data": [{"symbol": "BTCUSDT", "side": "Buy", "size": "0"}]})

    assert lot.exit_reconciliation_required is True
    assert store.load_lots("OPEN")[0].exit_reconciliation_required is True
    assert ("BTCUSDT", "LONG") not in engine.positions


def test_stale_timed_out_exit_with_no_exchange_position_is_held(env, monkeypatch):
    settings, store, telegram, adapter = env
    settings.enable_live_trading = True
    engine = Engine(settings, store, telegram, adapter=adapter)
    lot = EntryLot(
        lot_id="LOT-STALE-EXIT",
        symbol="BTCUSDT",
        side="LONG",
        qty=1.0,
        entry_price=100.0,
        entry_time_ms=1,
    )
    pending = PendingOrder(
        "SUBMITTING:STALE",
        "LINK-STALE",
        "BTCUSDT",
        "LONG",
        "EXIT",
        0,
        1.0,
        reason="strategy_target_then_profitable",
        created_at_ms=0,
        lot_id=lot.lot_id,
        status="SUBMITTING",
    )
    engine.lots[lot.lot_id] = lot
    engine.pending[pending.order_id] = pending
    engine.positions[("BTCUSDT", "LONG")] = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1, managed=True
    )
    store.save_lot(lot)
    store.save_pending(pending)
    store.save_position(engine.positions[("BTCUSDT", "LONG")])
    adapter.positions = []
    monkeypatch.setattr("core.engine.time.time", lambda: 1000.0)

    engine.reconcile()

    assert pending.order_id not in engine.pending
    assert lot.exit_reconciliation_required is True
    assert store.load_lots("OPEN")[0].exit_reconciliation_required is True
    assert ("BTCUSDT", "LONG") not in engine.positions
