import logging
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.bybit import BybitAdapter


def make_adapter(wallet_result, margin_mode="REGULAR_MARGIN"):
    adapter = object.__new__(BybitAdapter)
    adapter.settings = SimpleNamespace(
        bybit_testnet=True,
        bybit_api_key="",
        bybit_api_secret="",
    )
    adapter.http = Mock()
    adapter.http.get_wallet_balance.return_value = {
        "retCode": 0,
        "result": {"list": [wallet_result]},
    }
    adapter.http.get_account_info.return_value = {
        "retCode": 0,
        "result": {"marginMode": margin_mode, "unifiedMarginStatus": 5},
    }
    adapter.instruments = {}
    adapter.last_http_refresh = time.time()
    adapter.http_refresh_interval = 3600
    return adapter


def isolated_wallet(
    *,
    wallet_balance="51.8372",
    position_im="0",
    order_im="0",
    locked="0",
    bonus="0",
    total_equity="51.78041907",
):
    return {
        "totalEquity": total_equity,
        "totalAvailableBalance": "",
        "coin": [{
            "coin": "USDT",
            "walletBalance": wallet_balance,
            "totalPositionIM": position_im,
            "totalOrderIM": order_im,
            "locked": locked,
            "bonus": bonus,
        }],
    }


@pytest.mark.parametrize("raw_available", [None, "", "   "])
def test_blank_available_balance_fails_closed_when_mode_is_not_isolated(raw_available, caplog):
    adapter = make_adapter({
        "totalEquity": "51.78041907",
        "totalAvailableBalance": raw_available,
    })

    with caplog.at_level(logging.WARNING, logger="BYBIT_ADAPTER"):
        equity, available = adapter.get_balance()

    assert equity == pytest.approx(51.78041907)
    assert available == 0.0
    assert "totalAvailableBalance" in caplog.text
    assert "remains 0" in caplog.text
    adapter.http.get_wallet_balance.assert_called_once_with(
        accountType="UNIFIED", coin="USDT"
    )
    adapter.http.get_account_info.assert_called_once_with()


@pytest.mark.parametrize("raw_available", ["NaN", "Infinity", "-1", "not-a-number"])
def test_invalid_available_balance_fails_closed(raw_available, caplog):
    adapter = make_adapter({
        "totalEquity": "51.78041907",
        "totalAvailableBalance": raw_available,
    })

    with caplog.at_level(logging.ERROR, logger="BYBIT_ADAPTER"):
        equity, available = adapter.get_balance()

    assert equity == pytest.approx(51.78041907)
    assert available == 0.0
    assert "fail closed" in caplog.text


def test_valid_available_balance_is_preserved():
    adapter = make_adapter({
        "totalEquity": "51.78041907",
        "totalAvailableBalance": "40.125",
    })

    equity, available = adapter.get_balance()

    assert equity == pytest.approx(51.78041907)
    assert available == pytest.approx(40.125)
    adapter.http.get_account_info.assert_called_once_with()


def test_isolated_margin_uses_bybit_documented_usdt_available_balance_formula(caplog):
    adapter = make_adapter(isolated_wallet(), margin_mode="ISOLATED_MARGIN")

    with caplog.at_level(logging.INFO, logger="BYBIT_ADAPTER"):
        equity, available = adapter.get_balance()

    assert equity == pytest.approx(51.78041907)
    assert available == pytest.approx(51.8372)
    assert "documented formula" in caplog.text
    assert "51.83720000 USDT" in caplog.text


def test_isolated_margin_subtracts_position_order_locked_and_bonus():
    adapter = make_adapter(
        isolated_wallet(
            wallet_balance="51.8372",
            position_im="12.0",
            order_im="3.0",
            locked="1.0",
            bonus="2.0",
        ),
        margin_mode="ISOLATED_MARGIN",
    )

    _, available = adapter.get_balance()

    assert available == pytest.approx(33.8372)


def test_isolated_margin_missing_component_fails_closed(caplog):
    adapter = make_adapter(
        isolated_wallet(order_im=""),
        margin_mode="ISOLATED_MARGIN",
    )

    with caplog.at_level(logging.ERROR, logger="BYBIT_ADAPTER"):
        _, available = adapter.get_balance()

    assert available == 0.0
    assert "missing/invalid USDT totalOrderIM" in caplog.text


def test_isolated_margin_negative_computed_balance_fails_closed(caplog):
    adapter = make_adapter(
        isolated_wallet(wallet_balance="2", position_im="3"),
        margin_mode="ISOLATED_MARGIN",
    )

    with caplog.at_level(logging.ERROR, logger="BYBIT_ADAPTER"):
        _, available = adapter.get_balance()

    assert available == 0.0
    assert "invalid value" in caplog.text


def test_isolated_balance_can_proceed_to_positive_order_sizing():
    adapter = make_adapter(isolated_wallet(), margin_mode="ISOLATED_MARGIN")
    _, available = adapter.get_balance()
    adapter.settings.min_trade_usdt = 5.0
    adapter.settings.position_size_pct = 10.0
    adapter.instruments["ETHUSDT"] = {
        "lotSizeFilter": {
            "qtyStep": "0.001",
            "minOrderQty": "0.001",
            "maxOrderQty": "1000",
            "minNotionalValue": "5",
        },
        "priceFilter": {},
    }

    qty = adapter.size_from_balance("ETHUSDT", available, 10.0, 1000.0)

    assert qty > 0
    assert qty * 1000.0 <= available

@pytest.mark.parametrize("margin_mode", ["REGULAR_MARGIN", "PORTFOLIO_MARGIN", "", None])
def test_blank_available_balance_does_not_apply_isolated_formula_to_other_modes(margin_mode):
    adapter = make_adapter(isolated_wallet(), margin_mode=margin_mode or "")

    _, available = adapter.get_balance()

    assert available == 0.0


def test_account_info_failure_keeps_balance_zero(caplog):
    adapter = make_adapter(isolated_wallet(), margin_mode="ISOLATED_MARGIN")
    adapter.http.get_account_info.side_effect = RuntimeError("API unavailable")

    with caplog.at_level(logging.ERROR, logger="BYBIT_ADAPTER"):
        _, available = adapter.get_balance()

    assert available == 0.0
    assert "Could not verify Bybit margin mode" in caplog.text


def test_missing_usdt_coin_row_keeps_balance_zero(caplog):
    adapter = make_adapter({
        "totalEquity": "51.78041907",
        "totalAvailableBalance": "",
        "coin": [{"coin": "USDC", "walletBalance": "100"}],
    }, margin_mode="ISOLATED_MARGIN")

    with caplog.at_level(logging.ERROR, logger="BYBIT_ADAPTER"):
        _, available = adapter.get_balance()

    assert available == 0.0
    assert "no USDT coin row" in caplog.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("walletBalance", ""),
        ("walletBalance", "NaN"),
        ("totalPositionIM", None),
        ("totalPositionIM", "-0.1"),
        ("totalOrderIM", "Infinity"),
        ("locked", "not-a-number"),
        ("bonus", "-1"),
    ],
)
def test_invalid_isolated_margin_component_keeps_balance_zero(field, value, caplog):
    values = {
        "walletBalance": "51.8372",
        "totalPositionIM": "0",
        "totalOrderIM": "0",
        "locked": "0",
        "bonus": "0",
    }
    values[field] = value
    adapter = make_adapter(
        {
            "totalEquity": "51.78041907",
            "totalAvailableBalance": "",
            "coin": [{"coin": "USDT", **values}],
        },
        margin_mode="ISOLATED_MARGIN",
    )

    with caplog.at_level(logging.ERROR, logger="BYBIT_ADAPTER"):
        _, available = adapter.get_balance()

    assert available == 0.0
    assert f"USDT {field}" in caplog.text


def test_isolated_mode_uses_coin_formula_even_if_account_wide_field_is_zero():
    adapter = make_adapter({
        "totalEquity": "51.78041907",
        "totalAvailableBalance": "0",
        "coin": [{
            "coin": "USDT",
            "walletBalance": "51.8372",
            "totalPositionIM": "0",
            "totalOrderIM": "0",
            "locked": "0",
            "bonus": "0",
        }],
    }, margin_mode="ISOLATED_MARGIN")

    _, available = adapter.get_balance()

    assert available == pytest.approx(51.8372)
    adapter.http.get_account_info.assert_called_once_with()

def test_unknown_margin_mode_does_not_trust_nonblank_account_balance():
    adapter = make_adapter({
        "totalEquity": "51.78041907",
        "totalAvailableBalance": "49.5",
    }, margin_mode="")

    _, available = adapter.get_balance()

    assert available == 0.0

