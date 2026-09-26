import backtest


def test_stacking_cannot_override_account_exposure_cap(monkeypatch):
    monkeypatch.setattr(backtest, "LEVERAGE", 3.0)
    portfolio = backtest.Portfolio(starting_capital=500.0, cash=500.0)
    portfolio.lots = [
        backtest.Lot("BTCUSDT", index, index, 100.0, 500.0 / 100.0, 500.0, 0.0)
        for index in range(3)
    ]
    portfolio.lots[1].notional = 500.0
    portfolio.lots[1].qty = 5.0
    portfolio.lots[2].notional = 500.0
    portfolio.lots[2].qty = 5.0

    assert backtest.open_notional(portfolio) == 1500.0
    assert backtest.allowed_exposure(portfolio, {"BTCUSDT": 100.0}) == 1500.0