import pandas as pd

import backtest
from strategies.ema_strategies import EMABandStrategy


def test_backtest_sizing_uses_available_balance_without_multiplying_leverage(monkeypatch):
    monkeypatch.setattr(backtest, "POSITION_SIZE_PCT", 10.0)
    monkeypatch.setattr(backtest, "MIN_TRADE_USDT", 15.0)
    monkeypatch.setattr(backtest, "LEVERAGE", 3.0)

    assert backtest.target_entry_notional(1000.0) == 100.0
    assert backtest.target_entry_notional(50.0) == 15.0


def test_backtest_keeps_live_exit_armed_until_profitable(monkeypatch):
    monkeypatch.setattr(backtest, "STRATEGY_MODE", "ema_band")
    monkeypatch.setattr(backtest, "EXIT_CANDLES", 2)
    monkeypatch.setattr(backtest, "EXIT_RSI", 75.0)
    lot = backtest.Lot("BTCUSDT", 1, 0, 100.0, 1.0, 100.0, 0.0)

    assert not backtest.strategy_exit_ready(lot, pd.Series({"rsi": 80.0}), 1, 1.0)
    assert backtest.strategy_exit_ready(lot, pd.Series({"rsi": 80.0}), 2, -1.0) is False
    assert lot.exit_armed
    assert backtest.strategy_exit_ready(lot, pd.Series({"rsi": 50.0}), 3, 1.0)


def test_daily_realized_pnl_uses_utc_day():
    day_ms = 24 * 60 * 60 * 1000
    portfolio = backtest.Portfolio(starting_capital=1000.0, cash=1000.0)
    portfolio.closed_trades.extend([
        backtest.ClosedTrade(
            symbol="BTCUSDT", entry_ts=day_ms - 1, exit_ts=day_ms - 1,
            entry_price=100.0, exit_price=90.0, qty=1.0,
            notional_in=100.0, notional_out=90.0, gross_pnl=-10.0,
            entry_fee=0.0, exit_fee=0.0, net_pnl=-10.0,
            exit_reason="TEST", hold_candles=1,
        ),
        backtest.ClosedTrade(
            symbol="BTCUSDT", entry_ts=day_ms, exit_ts=day_ms,
            entry_price=100.0, exit_price=105.0, qty=1.0,
            notional_in=100.0, notional_out=105.0, gross_pnl=5.0,
            entry_fee=0.0, exit_fee=0.0, net_pnl=5.0,
            exit_reason="TEST", hold_candles=1,
        ),
    ])

    assert backtest.daily_realized_pnl(portfolio, day_ms) == 5.0


def test_liquidation_buffer_exits_count_as_stops():
    portfolio = backtest.Portfolio(starting_capital=1000.0, cash=1000.0)
    lot = backtest.Lot("BTCUSDT", 1, 0, 100.0, 1.0, 100.0, 0.0)

    backtest.close_lot(portfolio, lot, 2, 1, 90.0, "LIQUIDATION_BUFFER_EXIT")

    assert portfolio.stop_count == 1


def test_backtest_indicators_and_entry_rule_use_live_strategy(monkeypatch):
    monkeypatch.setattr(backtest, "STRATEGY_MODE", "ema_band")
    monkeypatch.setattr(backtest, "EMA_FAST", 3)
    monkeypatch.setattr(backtest, "EMA_SLOW", 4)
    monkeypatch.setattr(backtest, "RSI_PERIOD", 2)
    monkeypatch.setattr(backtest, "EXIT_RSI", 75.0)
    monkeypatch.setattr(backtest, "COOLDOWN_CANDLES", 5)
    monkeypatch.setattr(backtest, "EXIT_CANDLES", 2)
    candles = pd.DataFrame({
        "timestamp": range(10),
        "close": [10.0, 10.2, 10.4, 10.1, 10.3, 10.7, 10.5, 10.6, 10.4, 10.8],
    })
    live_strategy = EMABandStrategy(3, 4, 2, 5, 2, 75.0)

    enriched_by_backtest = backtest.add_indicators(candles)
    enriched_by_live = live_strategy.enrich(candles)

    pd.testing.assert_frame_equal(
        enriched_by_backtest[["ema_fast", "ema_slow", "rsi"]],
        enriched_by_live[["ema_fast", "ema_slow", "rsi"]],
    )
    for _, row in enriched_by_backtest.iterrows():
        assert backtest.entry_signal(row) == live_strategy.entry_ready(row)
        assert backtest.exit_signal(row, 2) == live_strategy.exit_ready(row, 2)


def test_elapsed_candles_uses_candle_timestamps_across_gaps():
    timeframe_ms = backtest.TIMEFRAME_MS
    assert backtest.elapsed_candles(0, 5 * timeframe_ms) == 5
    assert backtest.elapsed_candles(0, 7 * timeframe_ms + 1) == 7
    assert backtest.elapsed_candles(timeframe_ms, 0) == 0
