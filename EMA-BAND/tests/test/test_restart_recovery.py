from core.engine import Engine
from core.models import EntryLot, PendingOrder


def test_restart_recovers_open_lot_and_pending_order(env):
    settings, store, telegram, adapter = env
    lot = EntryLot("LOT-1", "BTCUSDT", "LONG", 1, 100, 1000, "OID-1", "C1", 0.1, entry_candle_start=1000)
    pending = PendingOrder("OID-2", "LINK-2", "BTCUSDT", "LONG", "ENTRY", 0, 1, status="NEW", lot_id="LOT-2", cycle_id="C2")
    store.save_lot(lot)
    store.save_pending(pending)

    engine = Engine(settings, store, telegram, adapter=adapter)
    engine.positions = {(p.symbol, p.side): p for p in store.load_positions()}
    engine.lots = {l.lot_id: l for l in store.load_lots("OPEN")}
    engine.pending = {p.order_id: p for p in store.load_pending() if p.status in {"NEW", "PARTIAL", "SUBMITTING"}}

    assert "LOT-1" in engine.lots
    assert "OID-2" in engine.pending
