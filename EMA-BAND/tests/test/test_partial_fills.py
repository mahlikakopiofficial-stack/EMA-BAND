from core.engine import Engine
from core.models import PendingOrder


def test_entry_partial_fills_accumulate_without_double_count(env):
    settings, store, telegram, adapter = env
    engine = Engine(settings, store, telegram, adapter=adapter)
    p = PendingOrder("OID-1", "LINK-1", "BTCUSDT", "LONG", "ENTRY", 0, 1, status="PARTIAL", lot_id="LOT-1", cycle_id="C1", candle_start=1)
    engine.pending[p.order_id] = p
    store.save_pending(p)

    engine.on_execution({"data": [{"orderId": "OID-1", "execId": "E1", "execQty": "0.4", "execPrice": "100", "execFee": "0.04", "execTime": "1"}]})
    engine.on_execution({"data": [{"orderId": "OID-1", "execId": "E2", "execQty": "0.6", "execPrice": "101", "execFee": "0.06", "execTime": "2"}]})

    lot = engine.lots["LOT-1"]
    assert lot.qty == 1.0
    assert round(lot.entry_price, 6) == 100.6
    assert round(lot.entry_fee, 6) == 0.10
