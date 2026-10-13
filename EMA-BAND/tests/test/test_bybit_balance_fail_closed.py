import logging
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.bybit import BybitAdapter


def make_adapter(wallet_result):
    adapter = object.__new__(BybitAdapter)
    adapter.settings = SimpleNamespace(
        bybit_testnet=True,
        bybit_api_key='',
        bybit_api_secret='',
    )
    adapter.http = Mock()
    adapter.http.get_wallet_balance.return_value = {
        'retCode': 0,
        'result': {'list': [wallet_result]},
    }
    adapter.instruments = {}
    adapter.last_http_refresh = time.time()
    adapter.http_refresh_interval = 3600
    return adapter


@pytest.mark.parametrize('raw_available', [None, '', '   '])
def test_blank_available_balance_uses_isolated_margin_coin_formula(raw_available, caplog):
    adapter = make_adapter({
        'totalEquity': '51.78041907',
        'totalAvailableBalance': raw_available,
        'coin': [{
            'coin': 'USDT',
            'walletBalance': '51.8372',
            'totalPositionIM': '2',
            'totalOrderIM': '3',
            'locked': '1',
            'bonus': '0.5',
        }],
    })
    adapter.http.get_account_info.return_value = {
        'retCode': 0,
        'result': {'marginMode': 'ISOLATED_MARGIN'},
    }

    with caplog.at_level(logging.INFO, logger='BYBIT_ADAPTER'):
        equity, available = adapter.get_balance()

    assert equity == pytest.approx(51.78041907)
    assert available == pytest.approx(45.3372)
    assert 'coin-level formula' in caplog.text
    adapter.http.get_wallet_balance.assert_called_once_with(
        accountType='UNIFIED', coin='USDT'
    )
    adapter.http.get_account_info.assert_called_once_with()


def test_blank_available_balance_uses_all_documented_isolated_fields():
    adapter = make_adapter({
        'totalEquity': '51.788',
        'totalAvailableBalance': '',
        'coin': [{
            'coin': 'USDT',
            'walletBalance': '51.8372',
            'totalPositionIM': '0',
            'totalOrderIM': '0',
            'locked': '0',
            'bonus': '0',
        }],
    })
    adapter.http.get_account_info.return_value = {
        'retCode': 0,
        'result': {'marginMode': 'ISOLATED_MARGIN'},
    }

    _, available = adapter.get_balance()

    assert available == pytest.approx(51.8372)


def test_blank_available_balance_does_not_apply_isolated_formula_to_other_modes(caplog):
    adapter = make_adapter({
        'totalEquity': '51.78041907',
        'totalAvailableBalance': '',
        'coin': [{
            'coin': 'USDT',
            'walletBalance': '51.8372',
            'totalPositionIM': '0',
            'totalOrderIM': '0',
            'locked': '0',
            'bonus': '0',
        }],
    })
    adapter.http.get_account_info.return_value = {
        'retCode': 0,
        'result': {'marginMode': 'REGULAR_MARGIN'},
    }

    with caplog.at_level(logging.ERROR, logger='BYBIT_ADAPTER'):
        _, available = adapter.get_balance()

    assert available == 0.0
    assert 'not ISOLATED_MARGIN' in caplog.text


def test_blank_available_balance_fails_closed_if_isolated_fields_missing(caplog):
    adapter = make_adapter({
        'totalEquity': '51.78041907',
        'totalAvailableBalance': '',
        'coin': [{
            'coin': 'USDT',
            'walletBalance': '51.8372',
            'totalPositionIM': '0',
            'totalOrderIM': '0',
            # locked is deliberately omitted; missing data must not be assumed zero.
            'bonus': '0',
        }],
    })
    adapter.http.get_account_info.return_value = {
        'retCode': 0,
        'result': {'marginMode': 'ISOLATED_MARGIN'},
    }

    with caplog.at_level(logging.ERROR, logger='BYBIT_ADAPTER'):
        _, available = adapter.get_balance()

    assert available == 0.0
    assert 'USDT locked is missing' in caplog.text


@pytest.mark.parametrize('raw_available', ['NaN', 'Infinity', '-1', 'not-a-number'])
def test_invalid_available_balance_fails_closed(raw_available, caplog):
    adapter = make_adapter({
        'totalEquity': '51.78041907',
        'totalAvailableBalance': raw_available,
    })

    with caplog.at_level(logging.ERROR, logger='BYBIT_ADAPTER'):
        equity, available = adapter.get_balance()

    assert equity == pytest.approx(51.78041907)
    assert available == 0.0
    assert 'fail closed' in caplog.text


def test_valid_available_balance_is_preserved():
    adapter = make_adapter({
        'totalEquity': '51.78041907',
        'totalAvailableBalance': '40.125',
    })

    equity, available = adapter.get_balance()

    assert equity == pytest.approx(51.78041907)
    assert available == pytest.approx(40.125)
