from core.engine import Engine
from core.models import PendingOrder


def test_reconcile_adopts_exchange_order_id_and_execution(env):
    settings, store, telegram, adapter = env
    engine = Engine(settings, store, telegram, adapter=adapter)
    p = PendingOrder("SUBMITTING:1", "LINK-1", "BTCUSDT", "LONG", "ENTRY", 0, 1, status="SUBMITTING", lot_id="LOT-1", cycle_id="C1", candle_start=1)
    engine.pending[p.order_id] = p
    store.save_pending(p)
    adapter.order_history["OID-9"] = {
        "orderId": "OID-9", "orderLinkId": "LINK-1", "symbol": "BTCUSDT",
        "orderStatus": "Filled", "cumExecQty": "1", "avgPrice": "100"
    }
    adapter.executions["OID-9"] = [{
        "orderId": "OID-9", "execId": "EX-9", "symbol": "BTCUSDT",
        "execQty": "1", "execPrice": "100", "execFee": "0.1", "execTime": "1"
    }]
    engine.reconcile()
    assert "OID-9" not in engine.pending
    assert "LOT-1" in engine.lots
    assert engine.lots["LOT-1"].qty == 1.0


def test_reconcile_blocks_silent_loss_of_managed_position(env):
    settings, store, telegram, adapter = env
    engine = Engine(settings, store, telegram, adapter=adapter)
    from core.models import Position
    pos = Position("BTCUSDT", "LONG", 1, 100, 1, managed=True)
    engine.positions[("BTCUSDT", "LONG")] = pos
    store.save_position(pos)
    adapter.positions = [{"symbol": "BTCUSDT", "side": "Buy", "size": "1", "avgPrice": "101"}]
    engine.reconcile()
    assert engine.positions[("BTCUSDT", "LONG")].qty == 1.0
    assert engine.positions[("BTCUSDT", "LONG")].entry_price == 101.0
