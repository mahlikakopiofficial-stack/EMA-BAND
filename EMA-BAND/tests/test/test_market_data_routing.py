import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

from config import Settings
from core.bybit import BybitAdapter
from core.websocket import MarketStream


def test_paper_mode_uses_mainnet_public_data_by_default(monkeypatch):
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.delenv("PAPER_MARKET_DATA_TESTNET", raising=False)

    settings = Settings()

    assert settings.bybit_testnet is True
    assert settings.enable_live_trading is False
    assert settings.market_data_testnet is False


def test_paper_mode_can_explicitly_select_testnet_market_data(monkeypatch):
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("PAPER_MARKET_DATA_TESTNET", "true")

    assert Settings().market_data_testnet is True


def test_live_market_data_follows_trading_environment(monkeypatch):
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "true")
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    monkeypatch.setenv("PAPER_MARKET_DATA_TESTNET", "false")
    assert Settings().market_data_testnet is True

    monkeypatch.setenv("BYBIT_TESTNET", "false")
    assert Settings().market_data_testnet is False


def test_public_rest_calls_use_market_client_not_trading_client():
    adapter = object.__new__(BybitAdapter)
    adapter.settings = SimpleNamespace(
        bybit_testnet=True,
        market_data_testnet=False,
        bybit_api_key="",
        bybit_api_secret="",
        symbols=("LINKUSDT",),
        timeframe="15",
    )
    adapter.http = Mock()
    adapter.market_http = Mock()
    adapter.instruments = {}
    adapter.last_http_refresh = time.time()
    adapter.http_refresh_interval = 3600

    adapter.market_http.get_instruments_info.return_value = {
        "retCode": 0,
        "result": {"list": [{
            "symbol": "LINKUSDT",
            "lotSizeFilter": {"minOrderQty": "0.01", "qtyStep": "0.01", "minNotionalValue": "5"},
            "priceFilter": {"tickSize": "0.001"},
        }]},
    }
    adapter.market_http.get_tickers.return_value = {
        "retCode": 0,
        "result": {"list": [{
            "symbol": "LINKUSDT", "lastPrice": "12.89",
            "bid1Price": "12.88", "ask1Price": "12.90",
            "price24hPcnt": "0.01", "volume24h": "1000",
        }]},
    }
    adapter.market_http.get_kline.return_value = {
        "retCode": 0,
        "result": {"list": [[
            "1700000000000", "12.0", "13.0", "11.0", "12.5", "100.0"
        ]]},
    }

    adapter.load_instruments()
    tickers = adapter.tickers()
    candles = adapter.candles("LINKUSDT")

    assert adapter.instruments["LINKUSDT"]["symbol"] == "LINKUSDT"
    assert tickers["LINKUSDT"]["last"] == 12.89
    assert candles[0]["close"] == 12.5
    adapter.market_http.get_instruments_info.assert_called_once()
    adapter.market_http.get_tickers.assert_called_once()
    adapter.market_http.get_kline.assert_called_once()
    adapter.http.get_instruments_info.assert_not_called()
    adapter.http.get_tickers.assert_not_called()
    adapter.http.get_kline.assert_not_called()


def test_market_websocket_uses_paper_market_data_source(monkeypatch):
    instances = []

    class FakeWebSocket:
        def __init__(self, *, testnet, channel_type):
            self.testnet = testnet
            self.channel_type = channel_type
            self.subscriptions = []
            instances.append(self)

        def kline_stream(self, *, interval, symbol, callback):
            self.subscriptions.append((interval, symbol))

    monkeypatch.setitem(
        sys.modules,
        "pybit.unified_trading",
        SimpleNamespace(WebSocket=FakeWebSocket),
    )
    settings = SimpleNamespace(
        bybit_testnet=True,
        market_data_testnet=False,
        symbols=("LINKUSDT",),
    )
    stream = MarketStream(settings, lambda candle: None)

    assert stream.start() is True
    assert len(instances) == 1
    assert instances[0].testnet is False
    assert instances[0].channel_type == "linear"
    assert instances[0].subscriptions == [(15, "LINKUSDT")]
