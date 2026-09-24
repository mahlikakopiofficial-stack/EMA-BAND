from core.bybit import BybitAdapter


def adapter_with_instrument(min_notional=5, step="0.1", minimum="0.1", maximum="1000"):
    a = object.__new__(BybitAdapter)
    a.instruments = {
        "BTCUSDT": {
            "lotSizeFilter": {
                "qtyStep": step,
                "minOrderQty": minimum,
                "maxOrderQty": maximum,
                "minNotionalValue": str(min_notional),
            },
            "priceFilter": {},
        }
    }
    return a


def test_size_uses_percentage_and_rounds_up_to_step():
    a = adapter_with_instrument()
    class S:
        min_trade_usdt = 5
    a.settings = S()
    qty = a.size_from_balance("BTCUSDT", 100, 10, 20)
    assert qty == 0.5


def test_size_uses_exchange_minimum_notional():
    a = adapter_with_instrument(min_notional=25)
    class S:
        min_trade_usdt = 5
    a.settings = S()
    qty = a.size_from_balance("BTCUSDT", 100, 1, 10)
    assert qty * 10 >= 25


def test_size_rejects_when_minimum_order_exceeds_available_balance():
    a = adapter_with_instrument(min_notional=25)
    class S:
        min_trade_usdt = 5
    a.settings = S()
    try:
        a.size_from_balance("BTCUSDT", 10, 1, 10)
    except ValueError as exc:
        assert "exceeds available balance" in str(exc)
    else:
        raise AssertionError("expected ValueError")
