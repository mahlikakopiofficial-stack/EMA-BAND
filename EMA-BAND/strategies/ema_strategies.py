from __future__ import annotations
from dataclasses import dataclass
import math
import pandas as pd


@dataclass(frozen=True)
class Signal:
    """Unified entry signal shape used by both strategies."""
    symbol: str
    side: str
    candle_start: int
    close: float
    reason: str


def _rsi(close: pd.Series, period: int = 20) -> pd.Series:
    """Wilder's RSI (the standard RSI definition used by TradingView/most platforms)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, float('nan'))
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 AND avg_gain > 0 is a genuine straight-up move -> RSI 100.
    # FIXED: the previous version used .where(avg_loss > 0, 100.0), which also turned
    # NaN into 100 -- so RSI read 100 during warmup and in a perfectly flat market.
    # RSI 100 >= the exit threshold, so that could arm an exit prematurely.
    straight_up = (avg_loss == 0) & (avg_gain > 0)
    rsi = rsi.mask(straight_up, 100.0)
    return rsi


class EMABandStrategy:
    """
    STRATEGY A (long only):
      Entry : 15m confirmed close sits between EMA200 and EMA210 (inclusive).
      Cooldown : after any entry attempt on a symbol, no new entry is
                 evaluated on that symbol until `cooldown_candles` (5)
                 further confirmed candles have printed. This is enforced
                 by the Engine (see next_entry_allowed_candle), not here.
      Exit  : only becomes eligible once `exit_candles` (0) candles have
              elapsed since the lot's entry candle AND RSI(20) >= exit_rsi
              (75.0). Even once eligible, the Engine will only actually close
              the lot when it is net-profitable; if it is underwater it is
              held (an independent hard stop-loss, configured separately in
              Settings, is the real safety net for that case).
    """
    def __init__(self, ema_fast=200, ema_slow=210, rsi_period=20,
                 cooldown_candles=0, exit_candles=0, exit_rsi=75.0):
        self.ema_fast = int(ema_fast)
        self.ema_slow = int(ema_slow)
        self.rsi_period = int(rsi_period)
        self.cooldown_candles = int(cooldown_candles)
        self.exit_candles = int(exit_candles)
        self.exit_rsi = float(exit_rsi)
        self.min_bars = max(self.ema_fast, self.ema_slow, self.rsi_period) + 1

    def enrich(self, candles):
        out = candles.copy()
        if out.empty:
            out['ema_fast'] = pd.Series(dtype=float)
            out['ema_slow'] = pd.Series(dtype=float)
            out['rsi'] = pd.Series(dtype=float)
            return out
        out['ema_fast'] = out['close'].ewm(span=self.ema_fast, adjust=False, min_periods=self.ema_fast).mean()
        out['ema_slow'] = out['close'].ewm(span=self.ema_slow, adjust=False, min_periods=self.ema_slow).mean()
        out['rsi'] = _rsi(out['close'], self.rsi_period)
        return out

    def signal(self, symbol, candles):
        if candles.empty or len(candles) < self.min_bars:
            return None
        row = self.enrich(candles).iloc[-1]
        close = float(row['close'])
        ema_f = float(row['ema_fast'])
        ema_s = float(row['ema_slow'])
        if not (math.isfinite(close) and math.isfinite(ema_f) and math.isfinite(ema_s)):
            return None
        lo, hi = (ema_f, ema_s) if ema_f <= ema_s else (ema_s, ema_f)
        if lo <= close <= hi:
            return Signal(symbol, 'LONG', int(row['start']), close,
                          f'price_between_ema{self.ema_fast}_ema{self.ema_slow}')
        return None

    def exit_ready(self, row, candles_elapsed: int) -> bool:
        """True once the time+RSI trigger condition is met (profit check
        happens separately in the Engine)."""
        try:
            rsi = float(row['rsi'])
        except (KeyError, TypeError, ValueError):
            return False
        if not math.isfinite(rsi):
            return False
        return candles_elapsed >= self.exit_candles and rsi >= self.exit_rsi


class EMARSIOversoldStrategy:
    """
    STRATEGY B (long only):
      Entry : 15m confirmed close is ABOVE EMA200 AND RSI(20) is BELOW
              entry_rsi (45).
      Exit  : becomes eligible once RSI(20) >= exit_rsi (80.0). As with
              Strategy A, the Engine only actually closes once the lot is
              net-profitable; a hard stop-loss covers the losing case.
      No cooldown by default (set entry_cooldown_candles=0 in Settings for
      this mode unless you want one).
    """
    def __init__(self, ema_period=200, rsi_period=20, entry_rsi=45.0, exit_rsi=80.0):
        self.ema_period = int(ema_period)
        self.rsi_period = int(rsi_period)
        self.entry_rsi = float(entry_rsi)
        self.exit_rsi = float(exit_rsi)
        self.min_bars = max(self.ema_period, self.rsi_period) + 1

    def enrich(self, candles):
        out = candles.copy()
        if out.empty:
            out['ema'] = pd.Series(dtype=float)
            out['rsi'] = pd.Series(dtype=float)
            return out
        out['ema'] = out['close'].ewm(span=self.ema_period, adjust=False, min_periods=self.ema_period).mean()
        out['rsi'] = _rsi(out['close'], self.rsi_period)
        return out

    def signal(self, symbol, candles):
        if candles.empty or len(candles) < self.min_bars:
            return None
        row = self.enrich(candles).iloc[-1]
        close = float(row['close'])
        ema = float(row['ema'])
        rsi = float(row['rsi'])
        if not (math.isfinite(close) and math.isfinite(ema) and math.isfinite(rsi)):
            return None
        if close > ema and rsi < self.entry_rsi:
            return Signal(symbol, 'LONG', int(row['start']), close,
                          f'price_above_ema{self.ema_period}_rsi_below_{int(self.entry_rsi)}')
        return None

    def exit_ready(self, row, candles_elapsed: int) -> bool:
        try:
            rsi = float(row['rsi'])
        except (KeyError, TypeError, ValueError):
            return False
        if not math.isfinite(rsi):
            return False
        return rsi >= self.exit_rsi

