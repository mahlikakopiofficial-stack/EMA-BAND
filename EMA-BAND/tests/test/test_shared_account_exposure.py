import pytest

import backtest


def test_symbols_share_one_margin_pool(monkeypatch):
    monkeypatch.setattr(backtest, "LEVERAGE", 3.0)
    portfolio = backtest.Portfolio(starting_capital=500.0, cash=500.0)
    portfolio.lots.extend([
        backtest.Lot("BTCUSDT", 1, 0, 100.0, 9.0, 900.0, 0.0),
        backtest.Lot("ETHUSDT", 1, 0, 100.0, 6.0, 600.0, 0.0),
    ])

    assert backtest.open_notional(portfolio) == pytest.approx(1500.0)
    assert backtest.margin_in_use(portfolio) == pytest.approx(500.0)
    assert backtest.available_margin(portfolio, {"BTCUSDT": 100.0, "ETHUSDT": 100.0}) == pytest.approx(0.0)