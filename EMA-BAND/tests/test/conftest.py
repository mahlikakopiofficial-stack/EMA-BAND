from pathlib import Path
from types import SimpleNamespace

import pytest

from core.persistence import Store


class DummyTelegram:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        return True


class DummyStream:
    def is_connected(self):
        return True

    def start(self):
        return True

    def stop(self):
        return None


class FakeAdapter:
    def __init__(self):
        self.orders = []
        self.order_history = {}
        self.executions = {}
        self.positions = []
        self.balance = (1000.0, 1000.0)
        self.tickers_data = {"BTCUSDT": {"last": 100.0, "bid": 99.9, "ask": 100.1}}
        self.instruments = {}
        self.next_order_id = 1
        self.raise_on_place = None
        self.raise_on_close = None

    def load_instruments(self):
        return None

    def set_leverage(self, symbol, leverage):
        return None

    def candles(self, symbol, limit=500):
        return []

    def tickers(self):
        return self.tickers_data

    def get_balance(self):
        return self.balance

    def size_from_balance(self, symbol, available_usdt, pct, price):
        return 1.0

    def place_market(self, symbol, side, qty, link, position_idx=0):
        if self.raise_on_place:
            exc = self.raise_on_place
            self.raise_on_place = None
            raise exc
        oid = f"OID-{self.next_order_id}"
        self.next_order_id += 1
        row = {
            "orderId": oid,
            "orderLinkId": link,
            "symbol": symbol,
            "side": "Buy" if side == "LONG" else "Sell",
            "orderStatus": "New",
            "cumExecQty": "0",
            "avgPrice": "0",
        }
        self.orders.append(row)
        self.order_history[oid] = row
        return {"retCode": 0, "result": {"orderId": oid}}

    def close_market(self, symbol, side, qty, link, position_idx=0):
        if self.raise_on_close:
            exc = self.raise_on_close
            self.raise_on_close = None
            raise exc
        oid = f"EXIT-{self.next_order_id}"
        self.next_order_id += 1
        row = {
            "orderId": oid,
            "orderLinkId": link,
            "symbol": symbol,
            "side": "Sell" if side == "LONG" else "Buy",
            "orderStatus": "New",
            "cumExecQty": "0",
            "avgPrice": "0",
        }
        self.orders.append(row)
        self.order_history[oid] = row
        return {"retCode": 0, "result": {"orderId": oid}}

    def get_positions(self):
        return list(self.positions)

    def get_order_history(self, order_id=None, order_link_id=None, symbol=None, limit=50):
        rows = list(self.order_history.values())
        if order_id:
            rows = [r for r in rows if r.get("orderId") == order_id]
        elif order_link_id:
            rows = [r for r in rows if r.get("orderLinkId") == order_link_id]
        elif symbol:
            rows = [r for r in rows if r.get("symbol") == symbol]
        return rows[:limit]

    def get_executions(self, symbol=None, order_id=None, order_link_id=None, limit=100):
        rows = []
        if order_id:
            rows = list(self.executions.get(order_id, []))
        elif symbol:
            for values in self.executions.values():
                rows.extend(x for x in values if x.get("symbol") == symbol)
        return rows[:limit]


def make_settings(tmp_path, **overrides):
    base = dict(
        bybit_testnet=True,
        enable_live_trading=False,
        category="linear",
        timeframe="15",
        symbols=("BTCUSDT",),
        enable_long=True,
        enable_short=False,
        position_size_pct=10.0,
        max_long_entries=999,
        min_trade_usdt=5.0,
        max_order_notional_usdt=0.0,
        leverage=3,
        stop_loss_pct=80.0,
        max_daily_loss_usdt=100.0,
        max_total_open_lots=50,
        require_leverage_confirmation=True,
        max_entry_price_deviation_pct=5.0,
        stuck_order_timeout_seconds=30,
        strategy_mode="ema_band",
        ema_band_fast=3,
        ema_band_slow=4,
        ema_band_rsi_period=2,
        ema_band_cooldown_candles=0,
        ema_band_exit_candles=0,
        ema_band_exit_rsi=75.0,
        ema_rsi_ema_period=3,
        ema_rsi_period=2,
        ema_rsi_entry_rsi=45.0,
        ema_rsi_exit_rsi=80.0,
        ema_rsi_cooldown_candles=0,
        taker_fee_rate=0.00055,
        scanner_interval_seconds=120.0,
        reconciliation_seconds=5,
        pnl_review_minutes=30,
        max_slippage_bps=50.0,
        telegram_bot_token="",
        telegram_chat_id="",
        telegram_hourly_pnl=False,
        telegram_pnl_interval_seconds=3600,
        telegram_notify_start_stop=False,
        kill_switch_file="EMERGENCY_STOP",
        require_env_file_permissions_check=False,
        dashboard_host="127.0.0.1",
        dashboard_port=8080,
        database_path=Path(tmp_path) / "data" / "trading.db",
        log_dir=Path(tmp_path) / "logs",
        bybit_api_key="",
        bybit_api_secret="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def env(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(settings.database_path)
    telegram = DummyTelegram()
    adapter = FakeAdapter()
    yield settings, store, telegram, adapter
    store.close()
