from core.engine import Engine


def signal():
    return type(
        "Signal",
        (),
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "candle_start": 1,
            "close": 100.0,
            "reason": "test",
        },
    )()


def connected_streams(engine):
    engine.ws_market = engine.ws_private = type(
        "S", (), {"is_connected": lambda self: True}
    )()


def test_live_entry_respects_shared_compounding_exposure_cap(env):
    settings, store, telegram, adapter = env
    settings.enable_live_trading = True
    settings.max_account_exposure_usdt = 50.0
    engine = Engine(settings, store, telegram, adapter=adapter)
    engine.leverage_confirmed.add("BTCUSDT")
    connected_streams(engine)

    assert engine.open_position(signal()) is False
    assert adapter.orders == []


def test_live_entry_rejects_when_required_margin_exceeds_available(env):
    settings, store, telegram, adapter = env
    settings.enable_live_trading = True
    adapter.balance = (1000.0, 10.0)
    engine = Engine(settings, store, telegram, adapter=adapter)
    engine.leverage_confirmed.add("BTCUSDT")
    connected_streams(engine)

    assert engine.open_position(signal()) is False
    assert adapter.orders == []