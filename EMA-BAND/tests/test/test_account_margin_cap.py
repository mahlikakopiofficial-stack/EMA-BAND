import pytest

import backtest


def make_portfolio(capital=500.0):
    return backtest.Portfolio(starting_capital=capital, cash=capital)


def make_lot(symbol, notional, price=100.0):
    return backtest.Lot(symbol, 1, 0, price, notional / price, notional, 0.0)


def test_500_usdt_at_3x_reserves_at_most_1500_notional(monkeypatch):
    monkeypatch.setattr(backtest, "LEVERAGE", 3.0)
    monkeypatch.setattr(backtest, "MAX_ACCOUNT_EXPOSURE_USDT", 0.0)
    portfolio = make_portfolio()
    portfolio.lots.append(make_lot("BTCUSDT", 1500.0))

    assert backtest.margin_in_use(portfolio) == pytest.approx(500.0)
    assert backtest.allowed_exposure(portfolio, {"BTCUSDT": 100.0}) == pytest.approx(1500.0)
    assert backtest.available_margin(portfolio, {"BTCUSDT": 100.0}) == pytest.approx(0.0)

    portfolio.lots.append(make_lot("ETHUSDT", 1.0))
    assert backtest.available_margin(
        portfolio,
        {"BTCUSDT": 100.0, "ETHUSDT": 100.0},
    ) == pytest.approx(0.0)