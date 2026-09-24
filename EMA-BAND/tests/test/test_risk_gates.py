from core.risk import RiskManager


def test_risk_accepts_clean_long_order(env):
    settings, store, _, _ = env
    risk = RiskManager(settings, store)
    ok, reason = risk.validate_order("BTCUSDT", "LONG", 1, {}, {})
    assert ok is True
    assert reason == "ok"


def test_risk_rejects_non_long():
    from conftest import make_settings
    from core.persistence import Store
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        s = make_settings(Path(d))
        store = Store(s.database_path)
        try:
            ok, reason = RiskManager(s, store).validate_order("BTCUSDT", "SHORT", 1, {}, {})
            assert not ok
            assert "only LONG" in reason
        finally:
            store.close()


def test_risk_rejects_duplicate_pending_entry(env):
    from core.models import PendingOrder
    settings, store, _, _ = env
    pending = {"1": PendingOrder("1", "L", "BTCUSDT", "LONG", "ENTRY", 0, 1, status="NEW")}
    ok, reason = RiskManager(settings, store).validate_order("BTCUSDT", "LONG", 1, {}, pending)
    assert not ok
    assert reason == "same-side entry pending"


def test_risk_rejects_unknown_exchange_position(env):
    from core.models import Position
    settings, store, _, _ = env
    positions = {("BTCUSDT", "LONG"): Position("BTCUSDT", "LONG", 1, 100, 1, managed=False)}
    ok, reason = RiskManager(settings, store).validate_order("BTCUSDT", "LONG", 1, positions, {})
    assert not ok
    assert "reconciliation" in reason
