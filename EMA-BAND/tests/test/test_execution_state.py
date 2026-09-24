from core.models import PendingOrder


def test_pending_order_lifecycle(env):
    settings, store, telegram, adapter = env
    settings.enable_live_trading = True
    # The production engine requires startup leverage confirmation before live entries.
    engine_leverage_symbol = "BTCUSDT"
    from core.engine import Engine

    engine = Engine(settings, store, telegram, adapter=adapter)
    engine.leverage_confirmed.add("BTCUSDT")
    engine.ws_market = engine.ws_private = type("S", (), {"is_connected": lambda self: True})()
    signal = type("Signal", (), {"symbol": "BTCUSDT", "side": "LONG", "candle_start": 1, "close": 100.0, "reason": "test"})()

    assert engine.open_position(signal) is True
    assert len(engine.pending) == 1
    pending = next(iter(engine.pending.values()))
    assert pending.status == "NEW"
    assert pending.order_id.startswith("OID-")


def test_order_callback_updates_state(env):
    settings, store, telegram, adapter = env
    from core.engine import Engine
    engine = Engine(settings, store, telegram, adapter=adapter)
    p = PendingOrder("OID-1", "LINK-1", "BTCUSDT", "LONG", "ENTRY", 0, 1, status="NEW")
    engine.pending[p.order_id] = p
    store.save_pending(p)
    engine.on_order({"data": [{"orderId": "OID-1", "orderLinkId": "LINK-1", "orderStatus": "PartiallyFilled", "cumExecQty": "0.4", "avgPrice": "100"}]})
    assert engine.pending["OID-1"].status == "PARTIAL"
    assert engine.pending["OID-1"].filled_qty == 0.4
