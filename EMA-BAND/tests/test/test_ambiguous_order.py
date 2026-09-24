import pytest

from core.bybit import BybitAdapter


def test_with_retry_retries_generic_api_failures():
    a = object.__new__(BybitAdapter)
    calls = {"n": 0}

    def f():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("temporary")
        return "ok"

    a.time = None
    result = a._with_retry(f, max_retries=3)
    assert result == "ok"
    assert calls["n"] == 3


def test_with_retry_does_not_retry_deterministic_value_error():
    a = object.__new__(BybitAdapter)
    calls = {"n": 0}

    def f():
        calls["n"] += 1
        raise ValueError("bad quantity")

    with pytest.raises(ValueError):
        a._with_retry(f, max_retries=3)
    assert calls["n"] == 1


def test_ambiguous_entry_is_left_pending_until_reconciliation(env):
    settings, store, telegram, adapter = env
    settings.enable_live_trading = True
    # The production engine requires startup leverage confirmation before live entries.
    engine_leverage_symbol = "BTCUSDT"
    from core.engine import Engine
    engine = Engine(settings, store, telegram, adapter=adapter)
    engine.leverage_confirmed.add("BTCUSDT")
    engine.ws_market = engine.ws_private = type("S", (), {"is_connected": lambda self: True})()
    def always_uncertain(*args, **kwargs):
        raise RuntimeError("timeout after submit")
    adapter.place_market = always_uncertain
    signal = type("Signal", (), {"symbol": "BTCUSDT", "side": "LONG", "candle_start": 1, "close": 100.0, "reason": "test"})()
    assert engine.open_position(signal) is False
    assert len(engine.pending) == 1
    p = next(iter(engine.pending.values()))
    assert p.status == "SUBMITTING"

    adapter.order_history["OID-AMB"] = {
        "orderId": "OID-AMB", "orderLinkId": p.order_link_id, "symbol": "BTCUSDT",
        "orderStatus": "Filled", "cumExecQty": "1", "avgPrice": "100"
    }
    adapter.executions["OID-AMB"] = [{
        "orderId": "OID-AMB", "execId": "EX-AMB", "symbol": "BTCUSDT",
        "execQty": "1", "execPrice": "100", "execFee": "0.1", "execTime": "1"
    }]
    engine.reconcile()
    assert "OID-AMB" not in engine.pending
    assert p.lot_id in engine.lots
