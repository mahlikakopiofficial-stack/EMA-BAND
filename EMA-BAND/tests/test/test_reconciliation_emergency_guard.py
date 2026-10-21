from core.engine import Engine
from core.models import EntryLot, Position


def test_reconciliation_hold_does_not_disable_verified_liquidation_buffer_exit(env):
    settings, store, telegram, adapter = env
    settings.enable_live_trading = True
    engine = Engine(settings, store, telegram, adapter=adapter)
    lot = EntryLot(
        lot_id="LOT-EMERGENCY-GUARD",
        symbol="BTCUSDT",
        side="LONG",
        qty=1.0,
        entry_price=100.0,
        entry_time_ms=1,
        exit_reconciliation_required=True,
    )
    pos = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1,
        managed=True, liq_price=80.0,
    )
    engine.lots[lot.lot_id] = lot
    engine.positions[("BTCUSDT", "LONG")] = pos
    store.save_lot(lot)
    store.save_position(pos)

    calls = []
    original_close = adapter.close_market

    def record_close(symbol, side, qty, link, position_idx=0):
        calls.append((symbol, side, qty, link, position_idx))
        return original_close(symbol, side, qty, link, position_idx)

    adapter.close_market = record_close

    # 30% of the entry-to-liquidation gap above an 80 liquidation price is 86.
    # At 85, the verified exchange position must still reach the emergency guard.
    engine._check_exits_locked("BTCUSDT", 85.0, None)

    assert len(calls) == 1
    assert calls[0][0:3] == ("BTCUSDT", "LONG", 1.0)
    assert len(adapter.orders) == 1


def test_failed_position_query_does_not_leave_stale_cached_position(env):
    settings, store, telegram, adapter = env
    settings.enable_live_trading = True
    engine = Engine(settings, store, telegram, adapter=adapter)
    lot = EntryLot(
        lot_id="LOT-QUERY-FAIL",
        symbol="BTCUSDT",
        side="LONG",
        qty=1.0,
        entry_price=100.0,
        entry_time_ms=1,
    )
    pos = Position(
        "BTCUSDT", "LONG", 1.0, 100.0, 1,
        managed=True, liq_price=80.0,
    )
    engine.lots[lot.lot_id] = lot
    engine.positions[("BTCUSDT", "LONG")] = pos
    store.save_lot(lot)
    store.save_position(pos)

    def fail_positions():
        raise RuntimeError("temporary position API failure")

    adapter.get_positions = fail_positions
    engine._hold_exit_for_reconciliation(
        "BTCUSDT", "LONG",
        reason="Bybit 110017", query_exchange=True,
    )

    assert lot.exit_reconciliation_required is True
    assert ("BTCUSDT", "LONG") not in engine.positions
    assert store.load_positions() == []
    assert store.load_lots("OPEN")[0].exit_reconciliation_required is True
