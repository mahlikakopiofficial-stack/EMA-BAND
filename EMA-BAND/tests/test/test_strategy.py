import pandas as pd

from strategies.ema_strategies import EMABandStrategy, EMARSIOversoldStrategy, _rsi


def candles(values):
    return pd.DataFrame({"start": range(len(values)), "close": values})


def test_ema_band_requires_warmup_and_signals_between_emas():
    s = EMABandStrategy(ema_fast=3, ema_slow=4, rsi_period=2)
    df = candles([10, 10.162986154509891, 10.321558015871743, 10.3835372230034, 10.553229294781934, 10.309961307895321, 10.480203056330724, 10.68778131302905, 10.5353126127384])
    signal = s.signal("BTCUSDT", df)
    assert signal is not None
    assert signal.side == "LONG"
    assert signal.symbol == "BTCUSDT"


def test_ema_rsi_requires_close_above_ema_and_rsi_below_entry():
    s = EMARSIOversoldStrategy(ema_period=3, rsi_period=2, entry_rsi=60, exit_rsi=80)
    df = candles([10, 9.630910701972548, 9.542544101105284, 9.93945855695876, 9.5846349774194, 9.173776952552378, 9.297543960881237, 8.884561026282618, 8.646763703465929, 8.947242618348358])
    enriched = s.enrich(df)
    row = enriched.iloc[-1]
    signal = s.signal("BTCUSDT", df)
    assert row["close"] > row["ema"]
    assert row["rsi"] < 60
    assert signal is not None


def test_rsi_warmup_does_not_return_100():
    series = _rsi(pd.Series([10.0, 10.0, 10.0]), period=2)
    assert pd.isna(series.iloc[0])
    assert pd.isna(series.iloc[1]) or series.iloc[1] != 100.0


def test_exit_ready_respects_rsi_and_candle_count():
    s = EMABandStrategy(ema_fast=3, ema_slow=4, rsi_period=2, exit_candles=2, exit_rsi=75)
    assert not s.exit_ready({"rsi": 80}, 1)
    assert s.exit_ready({"rsi": 80}, 2)
    assert not s.exit_ready({"rsi": 70}, 2)
