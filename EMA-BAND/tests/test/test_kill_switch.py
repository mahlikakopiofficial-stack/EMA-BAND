def test_kill_switch_blocks_new_entries(env):
    settings, store, telegram, adapter = env
    from core.engine import Engine
    kill = settings.log_dir.parent / settings.kill_switch_file
    kill.parent.mkdir(parents=True, exist_ok=True)
    kill.touch()
    engine = Engine(settings, store, telegram, adapter=adapter)
    signal = type("Signal", (), {"symbol": "BTCUSDT", "side": "LONG", "candle_start": 1, "close": 100.0, "reason": "test"})()
    assert engine.open_position(signal) is False
    assert any("KILL SWITCH ACTIVE" in msg for msg in telegram.messages)
