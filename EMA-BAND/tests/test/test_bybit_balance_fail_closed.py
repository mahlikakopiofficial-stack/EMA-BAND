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
def test_blank_available_balance_fails_closed(raw_available, caplog):
    adapter = make_adapter({
        'totalEquity': '51.78041907',
        'totalAvailableBalance': raw_available,
    })

    with caplog.at_level(logging.WARNING, logger='BYBIT_ADAPTER'):
        equity, available = adapter.get_balance()

    assert equity == pytest.approx(51.78041907)
    assert available == 0.0
    assert 'omitted totalAvailableBalance' in caplog.text
    assert 'fail closed' in caplog.text
    adapter.http.get_wallet_balance.assert_called_once_with(
        accountType='UNIFIED', coin='USDT'
    )


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
