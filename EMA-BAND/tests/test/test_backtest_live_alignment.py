import pandas as pd
from types import SimpleNamespace

import backtest
from core.engine import _build_strategy, _strategy_exit_allowed
from strategies.ema_strategies import EMABandStrategy, EMARSIOversoldStrategy


def test_backtest_sizing_uses_available_balance_without_multiplying_leverage(monkeypatch):
    monkeypatch.setattr(backtest, "POSITION_SIZE_PCT", 10.0)
    monkeypatch.setattr(backtest, "MIN_TRADE_USDT", 5.0)
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


def test_strategy_exits_require_confirmed_candle_when_enabled():
    settings = SimpleNamespace(exit_on_candle_close=True)
    assert _strategy_exit_allowed(settings, pd.Series({"confirm": True}))
    assert not _strategy_exit_allowed(settings, pd.Series({"confirm": False}))
    assert not _strategy_exit_allowed(settings, pd.Series({}))


def test_strategy_exits_can_be_intrabar_when_disabled():
    settings = SimpleNamespace(exit_on_candle_close=False)
    assert _strategy_exit_allowed(settings, pd.Series({"confirm": False}))
    assert _strategy_exit_allowed(settings, pd.Series({}))


def test_same_candle_sample_live_builder_and_backtest_match_for_both_strategies(monkeypatch):
    samples = {
        "ema_band": [
            *[100.0 - 0.01 * i for i in range(210)],
            *[100.0 - 0.01 * 209 + 0.04 * i for i in range(25)],
            *[100.0 - 0.01 * 209 + 0.04 * 24 + 3.0 * i for i in range(20)],
        ],
        "ema_rsi": [
            *([100.0] * 205),
            *[100.0 + 0.8 * (i + 1) for i in range(20)],
            *[116.0 - 1.8 * (i + 1) for i in range(15)],
            *[89.0 + 3.0 * (i + 1) for i in range(35)],
        ],
    }

    cases = [
        (
            "ema_band",
            SimpleNamespace(
                strategy_mode="ema_band",
                ema_band_fast=200,
                ema_band_slow=210,
                ema_band_rsi_period=20,
                ema_band_cooldown_candles=0,
                ema_band_exit_candles=5,
                ema_band_exit_rsi=75.0,
            ),
        ),
        (
            "ema_rsi",
            SimpleNamespace(
                strategy_mode="ema_rsi",
                ema_rsi_ema_period=200,
                ema_rsi_period=20,
                ema_rsi_entry_rsi=45.0,
                ema_rsi_exit_rsi=80.0,
            ),
        ),
    ]

    for mode, live_settings in cases:
        close = samples[mode]
        candles = pd.DataFrame({
            "start": [i * backtest.TIMEFRAME_MS for i in range(len(close))],
            "close": close,
        })

        monkeypatch.setattr(backtest, "STRATEGY_MODE", mode)
        monkeypatch.setattr(backtest, "EMA_FAST", 200)
        monkeypatch.setattr(backtest, "EMA_SLOW", 210 if mode == "ema_band" else 200)
        monkeypatch.setattr(backtest, "EMA_PERIOD", 200)
        monkeypatch.setattr(backtest, "RSI_PERIOD", 20)
        monkeypatch.setattr(backtest, "ENTRY_RSI", None if mode == "ema_band" else 45.0)
        monkeypatch.setattr(backtest, "EXIT_RSI", 75.0 if mode == "ema_band" else 80.0)
        monkeypatch.setattr(backtest, "COOLDOWN_CANDLES", 0)
        monkeypatch.setattr(backtest, "EXIT_CANDLES", 5 if mode == "ema_band" else 0)

        backtest._strategy_for.cache_clear()

        live_strategy = _build_strategy(live_settings)
        backtest_strategy = backtest.active_strategy()

        live_enriched = live_strategy.enrich(candles.copy())
        backtest_enriched = backtest_strategy.enrich(candles.copy())

        indicator_cols = ["ema_fast", "ema_slow", "rsi"] if mode == "ema_band" else ["ema", "rsi"]
        pd.testing.assert_frame_equal(
            live_enriched[indicator_cols],
            backtest_enriched[indicator_cols],
        )

        live_events = []
        backtest_events = []
        for i, row in live_enriched.iterrows():
            live_events.append((bool(live_strategy.entry_ready(row)), bool(live_strategy.exit_ready(row, i))))
        for i, row in backtest_enriched.iterrows():
            backtest_events.append((bool(backtest_strategy.entry_ready(row)), bool(backtest_strategy.exit_ready(row, i))))

        assert live_events == backtest_events
        assert any(entry for entry, _ in live_events), f"{mode}: sample did not produce an entry"
        assert any(exit_ready for _, exit_ready in live_events), f"{mode}: sample did not produce an exit-ready candle"
