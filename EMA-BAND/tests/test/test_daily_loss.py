from core.engine import Engine
from core.models import Trade
import calendar
import time


def test_daily_loss_blocks_new_entries(env, monkeypatch):
    settings, store, telegram, adapter = env
    settings.enable_live_trading = True
    engine = Engine(settings, store, telegram, adapter=adapter)
    engine.leverage_confirmed.add("BTCUSDT")
    engine.ws_market = engine.ws_private = type("S", (), {"is_connected": lambda self: True})()
    day_start = calendar.timegm(time.strptime("2026-09-24", "%Y-%m-%d")) * 1000
    store.trade(Trade("BTCUSDT", "LONG", 1, 100, 90, -150, 0, day_start + 1000, day_start + 2000, "C1", "test", "E", "X"))
    monkeypatch.setattr("core.engine.time.strftime", lambda fmt, tm=None: "2026-09-24")
    signal = type("Signal", (), {"symbol": "BTCUSDT", "side": "LONG", "candle_start": 1, "close": 100.0, "reason": "test"})()
    assert engine.open_position(signal) is False
    assert any("MAX DAILY LOSS" in msg for msg in telegram.messages)
