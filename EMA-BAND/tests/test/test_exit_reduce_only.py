
def test_close_market_uses_reduce_only_and_opposite_side(monkeypatch):
    from core.bybit import BybitAdapter

    class HTTP:
        def place_order(self, **kwargs):
            self.kwargs = kwargs
            return {"retCode": 0, "result": {"orderId": "X"}}

    a = object.__new__(BybitAdapter)
    a.settings = type("S", (), {"bybit_testnet": True, "bybit_api_key": "", "bybit_api_secret": "", "max_slippage_bps": 50})()
    a.http = HTTP()
    a.instruments = {"BTCUSDT": {"lotSizeFilter": {"qtyStep": "0.1", "minOrderQty": "0.1", "maxOrderQty": "1000"}, "priceFilter": {}}}
    a.last_http_refresh = 10**20
    a.http_refresh_interval = 3600
    result = a.close_market("BTCUSDT", "LONG", 1.0, "LINK", 0)
    assert result["result"]["orderId"] == "X"
    assert a.http.kwargs["side"] == "Sell"
    assert a.http.kwargs["reduceOnly"] is True
    assert a.http.kwargs["positionIdx"] == 0
