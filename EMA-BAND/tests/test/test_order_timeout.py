from core.engine import Engine
from core.models import PendingOrder


def test_stuck_entry_without_exchange_position_is_dropped(env, monkeypatch):
    settings, store, telegram, adapter = env
    engine = Engine(settings, store, telegram, adapter=adapter)
    p = PendingOrder("SUBMITTING:X", "LINK-X", "BTCUSDT", "LONG", "ENTRY", 0, 1, status="SUBMITTING", created_at_ms=0)
    engine.pending[p.order_id] = p
    store.save_pending(p)
    monkeypatch.setattr("core.engine.time.time", lambda: 1000.0)
    engine.reconcile()
    assert p.order_id not in engine.pending
    assert any(a["kind"] == "ENTRY_SUBMISSION_TIMEOUT" for a in store.recent_alerts())


def test_stuck_exit_is_dropped_for_retry(env, monkeypatch):
    settings, store, telegram, adapter = env
    engine = Engine(settings, store, telegram, adapter=adapter)
    p = PendingOrder("SUBMITTING:X", "LINK-X", "BTCUSDT", "LONG", "EXIT", 0, 1, status="SUBMITTING", created_at_ms=0, lot_id="LOT")
    engine.pending[p.order_id] = p
    store.save_pending(p)
    monkeypatch.setattr("core.engine.time.time", lambda: 1000.0)
    engine.reconcile()
    assert p.order_id not in engine.pending
