import pytest

import backtest


def test_realized_profit_compounds_account_exposure(monkeypatch):
    monkeypatch.setattr(backtest, "LEVERAGE", 3.0)
    portfolio = backtest.Portfolio(starting_capital=500.0, cash=500.0)
    lot = backtest.Lot("BTCUSDT", 1, 0, 100.0, 1.0, 100.0, 0.0)
    portfolio.lots.append(lot)
    backtest.close_lot(portfolio, lot, 2, 1, 200.0, "TEST")

    assert portfolio.realized_pnl == pytest.approx(100.0 - 200.0 * backtest.TAKER_FEE_RATE)
    assert portfolio.cash == pytest.approx(portfolio.starting_capital + portfolio.realized_pnl)
    assert backtest.allowed_exposure(portfolio, {}) == pytest.approx(portfolio.cash * 3.0)


def test_open_pnl_reconciles_after_entry_fee(monkeypatch):
    monkeypatch.setattr(backtest, "LEVERAGE", 3.0)
    portfolio = backtest.Portfolio(starting_capital=500.0, cash=500.0 - 1.0)
    lot = backtest.Lot("BTCUSDT", 1, 0, 100.0, 1.0, 100.0, 1.0)
    portfolio.lots.append(lot)
    ending_equity = backtest.current_equity(portfolio, {"BTCUSDT": 110.0})
    unrealized_net = ending_equity - portfolio.starting_capital - portfolio.realized_pnl

    assert unrealized_net == pytest.approx(10.0 - 1.0 - 110.0 * backtest.TAKER_FEE_RATE)