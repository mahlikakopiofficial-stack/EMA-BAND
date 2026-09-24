from core.engine import Engine
from core.models import PendingOrder


def test_duplicate_exec_id_is_ignored(env):
    settings, store, telegram, adapter = env
    engine = Engine(settings, store, telegram, adapter=adapter)
    p = PendingOrder("OID-1", "LINK-1", "BTCUSDT", "LONG", "ENTRY", 0, 1, status="PARTIAL", lot_id="LOT-1", cycle_id="C1", candle_start=1)
    engine.pending[p.order_id] = p
    store.save_pending(p)
    msg = {"data": [{"orderId": "OID-1", "execId": "DUP", "execQty": "1", "execPrice": "100", "execFee": "0.1", "execTime": "1"}]}
    engine.on_execution(msg)
    engine.on_execution(msg)
    qty, fee, avg = store.execution_totals("OID-1")
    assert qty == 1.0
    assert fee == 0.1
    assert avg == 100.0
