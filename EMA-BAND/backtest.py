from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from config import SETTINGS  # noqa: E402


# ============================================================
# FIXED BACKTEST SPECIFICATION
# ============================================================

TIMEFRAME = "15"
TIMEFRAME_MS = 15 * 60 * 1000

TEST_DAYS = 30

# ------------------------------------------------------------
# STRATEGY PARAMETERS - sourced from SETTINGS (.env / config.py),
# same STRATEGY_MODE switch used by the live engine. Nothing here
# is hardcoded: change STRATEGY_MODE / EMA_BAND_* / EMA_RSI_* in
# .env and rerun - no code edits needed.
# ------------------------------------------------------------
STRATEGY_MODE = getattr(SETTINGS, 'strategy_mode', 'ema_rsi')
if STRATEGY_MODE not in ('ema_band', 'ema_rsi'):
    raise SystemExit(
        f"STRATEGY_MODE must be 'ema_band' or 'ema_rsi' "
        f"(got {STRATEGY_MODE!r} - check .env)"
    )

if STRATEGY_MODE == 'ema_band':
    EMA_FAST = int(getattr(SETTINGS, 'ema_band_fast', 200))
    EMA_SLOW = int(getattr(SETTINGS, 'ema_band_slow', 210))
    RSI_PERIOD = int(getattr(SETTINGS, 'ema_band_rsi_period', 20))
    ENTRY_RSI = None  # ema_band does not use an entry RSI threshold
    EXIT_RSI = float(getattr(SETTINGS, 'ema_band_exit_rsi', 75.0))
    COOLDOWN_CANDLES = int(getattr(SETTINGS, 'ema_band_cooldown_candles', 5))
    EXIT_CANDLES = int(getattr(SETTINGS, 'ema_band_exit_candles', 0))
    EMA_PERIOD = EMA_FAST  # used only for WARMUP_BARS sizing below
else:  # 'ema_rsi'
    EMA_PERIOD = int(getattr(SETTINGS, 'ema_rsi_ema_period', 200))
    RSI_PERIOD = int(getattr(SETTINGS, 'ema_rsi_period', 20))
    ENTRY_RSI = float(getattr(SETTINGS, 'ema_rsi_entry_rsi', 45.0))
    EXIT_RSI = float(getattr(SETTINGS, 'ema_rsi_exit_rsi', 80.0))
    COOLDOWN_CANDLES = int(getattr(SETTINGS, 'ema_rsi_cooldown_candles', 0))
    EXIT_CANDLES = 0
    EMA_FAST = EMA_PERIOD
    EMA_SLOW = EMA_PERIOD

# Extra history needed before the test window.
WARMUP_BARS = max(EMA_FAST, EMA_SLOW) + RSI_PERIOD + 20

# Bybit request settings
BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
REQUEST_TIMEOUT = 20
REQUEST_RETRIES = 4

# IMPORTANT:
# Bybit may return fewer rows than requested.
# Do NOT stop pagination just because len(batch) < PAGE_LIMIT.
PAGE_LIMIT = 1000


# ============================================================
# SETTINGS FROM CONFIG
# ============================================================

POSITION_SIZE_PCT = float(
    getattr(SETTINGS, "position_size_pct", 10.0)
)

MIN_TRADE_USDT = float(
    getattr(SETTINGS, "min_trade_usdt", 5.0)
)

MAX_LONG_ENTRIES = int(
    getattr(SETTINGS, "max_long_entries", 999)
)

MAX_TOTAL_OPEN_LOTS = int(
    getattr(SETTINGS, "max_total_open_lots", 50)
)

MAX_ACCOUNT_EXPOSURE_USDT = float(
    getattr(SETTINGS, "max_account_exposure_usdt", 0.0)
)

LEVERAGE = float(
    getattr(SETTINGS, "leverage", 3.0)
)

TAKER_FEE_RATE = float(
    getattr(SETTINGS, "taker_fee_rate", 0.00055)
)

STOP_LOSS_PCT = float(
    getattr(SETTINGS, "stop_loss_pct", 80.0)
)

ENABLE_LONG = bool(
    getattr(SETTINGS, "enable_long", True)
)


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Lot:
    symbol: str
    entry_ts: int
    entry_idx: int
    entry_price: float
    qty: float
    notional: float
    entry_fee: float


@dataclass
class ClosedTrade:
    symbol: str
    entry_ts: int
    exit_ts: int
    entry_price: float
    exit_price: float
    qty: float
    notional_in: float
    notional_out: float
    gross_pnl: float
    entry_fee: float
    exit_fee: float
    net_pnl: float
    exit_reason: str
    hold_candles: int


@dataclass
class Portfolio:
    starting_capital: float
    cash: float

    realized_pnl: float = 0.0
    closed_wins: int = 0
    closed_losses: int = 0
    fees_paid: float = 0.0

    stop_count: int = 0
    entry_count: int = 0
    skipped_entries: int = 0
    skipped_min_trade: int = 0
    rejected_entries_by_reason: Dict[str, int] = field(default_factory=dict)
    max_used_margin: float = 0.0
    max_open_notional: float = 0.0
    max_account_leverage: float = 0.0
    max_concurrent_lots: int = 0

    lots: List[Lot] = field(default_factory=list)
    closed_trades: List[ClosedTrade] = field(default_factory=list)

    equity_curve: List[Tuple[int, float]] = field(
        default_factory=list
    )

    # Cash-only curve: moves only when a lot actually CLOSES (realized
    # P&L). Open/unrealized swings never touch this - used for a
    # drawdown figure that ignores still-open positions.
    realized_equity_curve: List[Tuple[int, float]] = field(
        default_factory=list
    )

    @property
    def closed_trades_count(self) -> int:
        return len(self.closed_trades)


# ============================================================
# HELPERS
# ============================================================

def utc_string(ms: int) -> str:
    return datetime.fromtimestamp(
        ms / 1000,
        tz=timezone.utc,
    ).strftime("%Y-%m-%d %H:%M:%S UTC")


def safe_float(value, default=0.0) -> float:
    try:
        x = float(value)

        if math.isfinite(x):
            return x

        return default

    except Exception:
        return default


# ============================================================
# BYBIT HTTP
# ============================================================

def request_json(params: dict) -> dict:
    last_error = None

    for attempt in range(
        1,
        REQUEST_RETRIES + 1,
    ):
        try:
            response = requests.get(
                BYBIT_KLINE_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            payload = response.json()

            if payload.get("retCode") != 0:
                raise RuntimeError(
                    f"Bybit retCode="
                    f"{payload.get('retCode')} "
                    f"retMsg="
                    f"{payload.get('retMsg')}"
                )

            return payload

        except Exception as exc:
            last_error = exc

            if attempt < REQUEST_RETRIES:
                time.sleep(0.8 * attempt)

    raise RuntimeError(
        f"Bybit request failed after "
        f"{REQUEST_RETRIES} attempts: "
        f"{last_error}"
    )


# ============================================================
# HISTORY DOWNLOAD
#
# IMPORTANT FIX:
# Continue paging until the requested time window is complete.
# Do NOT stop merely because Bybit returned fewer than PAGE_LIMIT.
# ============================================================

def fetch_history(
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> List[Candle]:

    rows: List[list] = []

    # Bybit Kline results are newest -> oldest.
    # Therefore pagination must move BACKWARD in time.
    page_end = end_ms - 1

    page = 0
    max_pages = 1000

    while page_end > start_ms:

        page += 1

        if page > max_pages:
            raise RuntimeError(
                f"{symbol}: pagination safety limit reached"
            )

        payload = request_json(
            {
                "category": "linear",
                "symbol": symbol,
                "interval": TIMEFRAME,
                "start": start_ms,
                "end": page_end,
                "limit": PAGE_LIMIT,
            }
        )

        batch = (
            payload
            .get("result", {})
            .get("list", [])
        )

        if not batch:
            break

        rows.extend(batch)

        timestamps = []

        for item in batch:
            try:
                timestamps.append(int(item[0]))
            except (TypeError, ValueError, IndexError):
                continue

        if not timestamps:
            break

        oldest_ts = min(timestamps)
        newest_ts = max(timestamps)

        print(
            f"{symbol}: "
            f"page={page} "
            f"rows={len(batch)} "
            f"oldest={utc_string(oldest_ts)} "
            f"newest={utc_string(newest_ts)}"
        )

        if oldest_ts <= start_ms:
            break

        next_page_end = oldest_ts - 1

        if next_page_end >= page_end:
            raise RuntimeError(
                f"{symbol}: pagination cursor did not move backward"
            )

        page_end = next_page_end

        time.sleep(0.08)

    unique_rows = {
        int(row[0]): row
        for row in rows
        if row
    }

    candles: List[Candle] = []

    for ts in sorted(unique_rows):

        row = unique_rows[ts]

        if not (start_ms <= ts < end_ms):
            continue

        try:
            candles.append(
                Candle(
                    ts=int(row[0]),
                    open=safe_float(row[1]),
                    high=safe_float(row[2]),
                    low=safe_float(row[3]),
                    close=safe_float(row[4]),
                    volume=safe_float(row[5]),
                )
            )

        except (TypeError, ValueError, IndexError):
            continue

    return candles


def candles_to_df(
    candles: List[Candle],
) -> pd.DataFrame:

    return pd.DataFrame(
        {
            "timestamp": [
                candle.ts
                for candle in candles
            ],
            "open": [
                candle.open
                for candle in candles
            ],
            "high": [
                candle.high
                for candle in candles
            ],
            "low": [
                candle.low
                for candle in candles
            ],
            "close": [
                candle.close
                for candle in candles
            ],
            "volume": [
                candle.volume
                for candle in candles
            ],
        }
    )


# ============================================================
# EMA + WILDER RSI
# ============================================================

def add_indicators(
    df: pd.DataFrame,
) -> pd.DataFrame:

    out = df.copy()

    close = (
        out["close"]
        .astype(float)
    )

    # --------------------------------------------------------
    # EMA(s) - depends on STRATEGY_MODE
    # --------------------------------------------------------

    if STRATEGY_MODE == "ema_band":

        out["ema_fast"] = close.ewm(
            span=EMA_FAST, adjust=False, min_periods=EMA_FAST,
        ).mean()

        out["ema_slow"] = close.ewm(
            span=EMA_SLOW, adjust=False, min_periods=EMA_SLOW,
        ).mean()

        # kept for any code path that still reads "ema"
        out["ema"] = out["ema_fast"]

    else:

        out["ema"] = (
            close
            .ewm(
                span=EMA_PERIOD,
                adjust=False,
                min_periods=EMA_PERIOD,
            )
            .mean()
        )

    # --------------------------------------------------------
    # Wilder RSI (period from active strategy's settings)
    # --------------------------------------------------------

    delta = close.diff()

    gain = delta.clip(
        lower=0.0
    )

    loss = (
        -delta.clip(
            upper=0.0
        )
    )

    avg_gain = (
        gain
        .ewm(
            alpha=1.0 / RSI_PERIOD,
            adjust=False,
            min_periods=RSI_PERIOD,
        )
        .mean()
    )

    avg_loss = (
        loss
        .ewm(
            alpha=1.0 / RSI_PERIOD,
            adjust=False,
            min_periods=RSI_PERIOD,
        )
        .mean()
    )

    rs = (
        avg_gain /
        avg_loss.replace(
            0.0,
            np.nan,
        )
    )

    out["rsi"] = (
        100.0 -
        (
            100.0 /
            (1.0 + rs)
        )
    )

    # Strong up-only sequence
    straight_up = (
        (avg_loss == 0) &
        (avg_gain > 0)
    )

    out.loc[
        straight_up,
        "rsi",
    ] = 100.0

    # Strong down-only sequence
    straight_down = (
        (avg_gain == 0) &
        (avg_loss > 0)
    )

    out.loc[
        straight_down,
        "rsi",
    ] = 0.0

    return out


# ============================================================
# ENTRY
# ============================================================

def entry_signal(
    row: pd.Series,
) -> bool:

    if not ENABLE_LONG:
        return False

    close = safe_float(row.get("close"), math.nan)

    if STRATEGY_MODE == "ema_band":

        ema_f = safe_float(row.get("ema_fast"), math.nan)
        ema_s = safe_float(row.get("ema_slow"), math.nan)

        if not all(math.isfinite(v) for v in (close, ema_f, ema_s)):
            return False

        lo, hi = (ema_f, ema_s) if ema_f <= ema_s else (ema_s, ema_f)
        return lo <= close <= hi

    # ema_rsi
    ema = safe_float(row.get("ema"), math.nan)
    rsi = safe_float(row.get("rsi"), math.nan)

    if not all(math.isfinite(v) for v in (close, ema, rsi)):
        return False

    return close > ema and rsi < ENTRY_RSI


# ============================================================
# EXIT
# ============================================================

def exit_signal(
    row: pd.Series,
    candles_elapsed: int = 0,
) -> bool:

    rsi = safe_float(row.get("rsi"), math.nan)

    if not math.isfinite(rsi):
        return False

    if STRATEGY_MODE == "ema_band" and candles_elapsed < EXIT_CANDLES:
        return False

    return rsi >= EXIT_RSI


# ============================================================
# PORTFOLIO
# ============================================================

def current_equity(
    portfolio: Portfolio,
    prices: Dict[str, float],
) -> float:

    equity = (
        portfolio.cash
    )

    for lot in portfolio.lots:

        price = prices.get(
            lot.symbol
        )

        if price is None:
            continue

        gross = (
            price -
            lot.entry_price
        ) * lot.qty

        estimated_exit_fee = (
            price *
            lot.qty *
            TAKER_FEE_RATE
        )

        equity += (
            gross -
            estimated_exit_fee
        )

    return equity


def unrealized_pnl(
    portfolio: Portfolio,
    prices: Dict[str, float],
) -> float:
    return current_equity(portfolio, prices) - portfolio.cash


def margin_in_use(
    portfolio: Portfolio,
) -> float:

    leverage = max(
        LEVERAGE,
        1.0,
    )

    return sum(
        lot.notional /
        leverage
        for lot in portfolio.lots
    )


def open_notional(portfolio: Portfolio) -> float:
    return sum(lot.notional for lot in portfolio.lots)


def allowed_exposure(portfolio: Portfolio, prices: Dict[str, float]) -> float:
    equity_limit = max(0.0, current_equity(portfolio, prices)) * max(LEVERAGE, 1.0)
    if MAX_ACCOUNT_EXPOSURE_USDT > 0:
        configured_limit = min(MAX_ACCOUNT_EXPOSURE_USDT, equity_limit)
    else:
        configured_limit = equity_limit

    # Losses can make existing positions temporarily exceed the new equity-based
    # limit; block further entries rather than failing to account for them.
    return max(configured_limit, open_notional(portfolio))


def available_margin(
    portfolio: Portfolio,
    prices: Dict[str, float],
) -> float:

    equity = (
        current_equity(
            portfolio,
            prices,
        )
    )

    used_margin = (
        margin_in_use(
            portfolio
        )
    )

    return max(
        0.0,
        equity -
        used_margin,
    )


def record_rejection(portfolio: Portfolio, reason: str) -> None:
    portfolio.skipped_entries += 1
    portfolio.rejected_entries_by_reason[reason] = (
        portfolio.rejected_entries_by_reason.get(reason, 0) + 1
    )


def assert_account_invariants(portfolio: Portfolio, prices: Dict[str, float]) -> None:
    tolerance = 1e-8
    used_margin = margin_in_use(portfolio)
    exposure = open_notional(portfolio)
    allowed_margin = max(0.0, allowed_exposure(portfolio, prices) / max(LEVERAGE, 1.0))
    assert used_margin <= allowed_margin + tolerance, (
        f"used margin {used_margin} exceeds allowed margin {allowed_margin}"
    )
    assert exposure <= allowed_exposure(portfolio, prices) + tolerance, (
        f"open exposure {exposure} exceeds allowed exposure {allowed_exposure(portfolio, prices)}"
    )
    assert all(lot.notional > 0 and lot.qty > 0 for lot in portfolio.lots)
    assert abs(sum(lot.notional / max(LEVERAGE, 1.0) for lot in portfolio.lots) - used_margin) <= tolerance


def update_account_diagnostics(portfolio: Portfolio, prices: Dict[str, float]) -> None:
    used_margin = margin_in_use(portfolio)
    exposure = open_notional(portfolio)
    equity = current_equity(portfolio, prices)
    portfolio.max_used_margin = max(portfolio.max_used_margin, used_margin)
    portfolio.max_open_notional = max(portfolio.max_open_notional, exposure)
    portfolio.max_concurrent_lots = max(portfolio.max_concurrent_lots, len(portfolio.lots))
    portfolio.max_account_leverage = max(
        portfolio.max_account_leverage,
        exposure / equity if equity > 0 else 0.0,
    )


# ============================================================
# CLOSE LOT
# ============================================================

def close_lot(
    portfolio: Portfolio,
    lot: Lot,
    exit_ts: int,
    exit_idx: int,
    exit_price: float,
    reason: str,
) -> ClosedTrade:

    gross_pnl = (
        exit_price -
        lot.entry_price
    ) * lot.qty

    exit_notional = (
        exit_price *
        lot.qty
    )

    exit_fee = (
        exit_notional *
        TAKER_FEE_RATE
    )

    net_pnl = (
        gross_pnl -
        lot.entry_fee -
        exit_fee
    )

    portfolio.cash += (
        gross_pnl -
        exit_fee
    )

    portfolio.realized_pnl += (
        net_pnl
    )

    portfolio.fees_paid += (
        exit_fee
    )

    if net_pnl > 0:
        portfolio.closed_wins += 1

    else:
        portfolio.closed_losses += 1

    if reason == "STOP":
        portfolio.stop_count += 1

    trade = ClosedTrade(
        symbol=lot.symbol,
        entry_ts=lot.entry_ts,
        exit_ts=exit_ts,
        entry_price=lot.entry_price,
        exit_price=exit_price,
        qty=lot.qty,
        notional_in=lot.notional,
        notional_out=exit_notional,
        gross_pnl=gross_pnl,
        entry_fee=lot.entry_fee,
        exit_fee=exit_fee,
        net_pnl=net_pnl,
        exit_reason=reason,
        hold_candles=max(
            0,
            exit_idx -
            lot.entry_idx,
        ),
    )

    portfolio.closed_trades.append(
        trade
    )

    return trade


# ============================================================
# ARGUMENTS
# ============================================================

def parse_args():

    parser = argparse.ArgumentParser(
        description=(
            "10-day EMA200 + RSI20 "
            "Bybit 15m backtest"
        )
    )

    parser.add_argument(
        "--days",
        type=int,
        default=30,
    )

    parser.add_argument(
        "--capital",
        type=float,
        default=1000.0,
    )

    parser.add_argument(
        "--symbol",
        action="append",
        dest="symbols",
        help=(
            "Run only this symbol. "
            "Can be repeated."
        ),
    )

    return parser.parse_args()


# ============================================================
# MAIN
# ============================================================

def main():

    args = parse_args()

    if args.days <= 0:
        raise SystemExit(
            "--days must be greater than 0"
        )

    if args.capital <= 0:
        raise SystemExit(
            "--capital must be greater than 0"
        )

    if args.days != 30:
        print(
            f"WARNING: requested "
            f"{args.days} days; "
            f"standard test is 30 days."
        )

    symbols = (
        list(args.symbols)
        if args.symbols
        else list(
            getattr(
                SETTINGS,
                "symbols",
                [],
            )
        )
    )

    if not symbols:
        raise SystemExit(
            "No symbols found "
            "in SETTINGS.symbols"
        )

    # --------------------------------------------------------
    # TEST WINDOW
    # --------------------------------------------------------

    now_ms = int(
        datetime.now(
            tz=timezone.utc
        ).timestamp() * 1000
    )

    completed_boundary = (
        now_ms //
        TIMEFRAME_MS
    ) * TIMEFRAME_MS

    test_end_ms = (
        completed_boundary
    )

    test_start_ms = (
        test_end_ms -
        args.days *
        24 *
        60 *
        60 *
        1000
    )

    download_start_ms = (
        test_start_ms -
        WARMUP_BARS *
        TIMEFRAME_MS
    )

    # --------------------------------------------------------
    # HEADER
    # --------------------------------------------------------

    print("=" * 120)

    print(
        f"STRATEGY_MODE={STRATEGY_MODE} - "
        "15-MINUTE SHARED-ACCOUNT BACKTEST"
    )

    print("=" * 120)

    if STRATEGY_MODE == "ema_band":
        print(
            f"Entry                : "
            f"close between EMA{EMA_FAST} and EMA{EMA_SLOW} "
            f"(cooldown {COOLDOWN_CANDLES} candles after each attempt)"
        )
        print(
            f"Exit trigger         : "
            f"{EXIT_CANDLES} candles since entry AND "
            f"RSI{RSI_PERIOD} >= {EXIT_RSI:g} "
            f"AND estimated net P&L > 0"
        )
    else:
        print(
            f"Entry                : "
            f"close > EMA{EMA_PERIOD} "
            f"AND RSI{RSI_PERIOD} "
            f"< {ENTRY_RSI:g}"
        )
        print(
            f"Exit trigger         : "
            f"RSI{RSI_PERIOD} "
            f">= {EXIT_RSI:g} "
            f"AND estimated net P&L > 0"
        )

    print(
        f"Hard stop            : "
        f"{STOP_LOSS_PCT:g}%"
    )

    print(
        f"Position size        : "
        f"{POSITION_SIZE_PCT:g}% "
        f"of available shared equity"
    )

    print(
        f"Minimum trade        : "
        f"{MIN_TRADE_USDT:g} USDT"
    )

    print(
        f"Max lots / symbol    : "
        f"{MAX_LONG_ENTRIES}"
    )

    print(
        f"Max total open lots  : "
        f"{MAX_TOTAL_OPEN_LOTS}"
        if MAX_TOTAL_OPEN_LOTS > 0
        else
        "Max total open lots  : DISABLED"
    )

    print(
        f"Leverage model       : "
        f"{LEVERAGE:g}x"
    )

    print(
        f"Taker fee / side     : "
        f"{TAKER_FEE_RATE * 100:.5f}%"
    )

    print(
        f"Starting capital     : "
        f"{args.capital:.2f} USDT TOTAL"
    )

    print(
        f"Timeframe            : "
        f"{TIMEFRAME}m"
    )

    print(
        f"Test window          : "
        f"{utc_string(test_start_ms)} "
        f"-> "
        f"{utc_string(test_end_ms)}"
    )

    print(
        f"Symbols              : "
        f"{len(symbols)}"
    )

    print(
        "Account model        : "
        "ONE SHARED PORTFOLIO "
        "ACROSS ALL SYMBOLS"
    )

    print(
        "No live orders are sent "
        "by this script."
    )

    print("=" * 120)

    # --------------------------------------------------------
    # DOWNLOAD
    # --------------------------------------------------------

    frames: Dict[
        str,
        pd.DataFrame
    ] = {}

    candle_indexes: Dict[
        str,
        Dict[int, int]
    ] = {}

    for number, symbol in enumerate(
        symbols,
        1,
    ):

        print(
            f"\n[{number}/{len(symbols)}] "
            f"{symbol} - "
            f"downloading 15m history..."
        )

        try:

            candles = fetch_history(
                symbol,
                download_start_ms,
                test_end_ms,
            )

        except Exception as exc:

            print(
                f"{symbol}: "
                f"ERROR - {exc}"
            )

            continue

        if len(candles) < (
            WARMUP_BARS + 10
        ):

            print(
                f"{symbol}: "
                f"SKIP - "
                f"only "
                f"{len(candles)} "
                f"candles"
            )

            continue

        df = candles_to_df(
            candles
        )

        df = add_indicators(
            df
        )

        # Keep exact test window.
        df = df[
            df["timestamp"] >=
            test_start_ms
        ].reset_index(
            drop=True
        )

        # Remove warmup NaN rows.
        df = df.dropna(
            subset=[
                "ema",
                "rsi",
            ]
        ).reset_index(
            drop=True
        )

        if df.empty:

            print(
                f"{symbol}: "
                "SKIP - "
                "no usable "
                "test-window candles"
            )

            continue

        frames[symbol] = df

        candle_indexes[symbol] = {
            int(ts): idx
            for idx, ts in enumerate(
                df["timestamp"]
            )
        }

        print(
            f"{symbol}: "
            f"{len(df):,} "
            f"completed 15m candles"
        )

    if not frames:

        raise SystemExit(
            "No usable symbol data."
        )

    # --------------------------------------------------------
    # CHRONOLOGICAL EVENT STREAM
    # --------------------------------------------------------

    timestamps = sorted(
        {
            int(ts)
            for df in frames.values()
            for ts in df["timestamp"]
        }
    )

    rows: Dict[
        str,
        Dict[int, pd.Series]
    ] = {}

    for symbol, df in frames.items():

        rows[symbol] = {
            int(row["timestamp"]): row
            for _, row in df.iterrows()
        }

    # --------------------------------------------------------
    # SHARED ACCOUNT
    # --------------------------------------------------------

    portfolio = Portfolio(
        starting_capital=args.capital,
        cash=args.capital,
    )

    open_lots: Dict[
        str,
        List[Lot]
    ] = {
        symbol: []
        for symbol in frames
    }

    last_prices: Dict[
        str,
        float
    ] = {}

    symbol_entries: Dict[
        str,
        int
    ] = {
        symbol: 0
        for symbol in frames
    }

    symbol_stops: Dict[
        str,
        int
    ] = {
        symbol: 0
        for symbol in frames
    }

    # Last candle index a symbol had an entry attempt, for COOLDOWN_CANDLES.
    last_entry_idx: Dict[str, int] = {symbol: -10**9 for symbol in frames}
    account_halted = False

    # --------------------------------------------------------
    # PROCESS
    # --------------------------------------------------------

    for ts in timestamps:

        # ----------------------------------------------------
        # UPDATE PRICE
        # ----------------------------------------------------

        for symbol, symbol_rows in rows.items():

            row = symbol_rows.get(ts)

            if row is not None:

                last_prices[symbol] = (
                    safe_float(
                        row["close"]
                    )
                )

        # ----------------------------------------------------
        # EXITS FIRST
        # ----------------------------------------------------

        for symbol, symbol_rows in rows.items():

            row = symbol_rows.get(ts)

            if row is None:
                continue

            current_idx = (
                candle_indexes[
                    symbol
                ].get(ts)
            )

            if current_idx is None:
                continue

            survivors: List[Lot] = []

            current_close = safe_float(
                row["close"]
            )

            for lot in open_lots[symbol]:

                # --------------------------------------------
                # HARD STOP
                # --------------------------------------------

                stop_price = None

                if STOP_LOSS_PCT > 0:

                    stop_price = (
                        lot.entry_price *
                        (
                            1.0 -
                            STOP_LOSS_PCT /
                            100.0
                        )
                    )

                if (
                    stop_price is not None
                    and
                    safe_float(
                        row["low"]
                    ) <= stop_price
                ):

                    close_lot(
                        portfolio,
                        lot,
                        ts,
                        current_idx,
                        stop_price,
                        "STOP",
                    )

                    symbol_stops[
                        symbol
                    ] += 1

                    continue

                # --------------------------------------------
                # ESTIMATED NET P&L
                # --------------------------------------------

                estimated_gross = (
                    current_close -
                    lot.entry_price
                ) * lot.qty

                estimated_exit_fee = (
                    current_close *
                    lot.qty *
                    TAKER_FEE_RATE
                )

                estimated_net = (
                    estimated_gross -
                    lot.entry_fee -
                    estimated_exit_fee
                )

                # --------------------------------------------
                # RSI EXIT
                # --------------------------------------------

                candles_elapsed = current_idx - lot.entry_idx

                if (
                    exit_signal(row, candles_elapsed)
                    and
                    estimated_net > 0
                ):

                    close_lot(
                        portfolio,
                        lot,
                        ts,
                        current_idx,
                        current_close,
                        "RSI_EXIT",
                    )

                    continue

                survivors.append(
                    lot
                )

            open_lots[symbol] = (
                survivors
            )

        portfolio.lots = [
            lot
            for lots in open_lots.values()
            for lot in lots
        ]

        # ----------------------------------------------------
        # ENTRIES
        # ----------------------------------------------------

        for symbol, symbol_rows in rows.items():

            row = symbol_rows.get(ts)

            if row is None:
                continue

            current_idx = (
                candle_indexes[
                    symbol
                ].get(ts)
            )

            if current_idx is None:
                continue

            portfolio.lots = [
                lot
                for lots in open_lots.values()
                for lot in lots
            ]

            # Per-symbol cooldown (COOLDOWN_CANDLES since last entry attempt).
            if (current_idx - last_entry_idx[symbol]) < COOLDOWN_CANDLES:
                continue

            if not entry_signal(row):
                continue

            last_entry_idx[symbol] = current_idx

            if account_halted:
                record_rejection(portfolio, "insufficient_equity")
                continue

            if len(open_lots[symbol]) >= MAX_LONG_ENTRIES:
                record_rejection(portfolio, "max_positions")
                continue

            if MAX_TOTAL_OPEN_LOTS > 0 and len(portfolio.lots) >= MAX_TOTAL_OPEN_LOTS:
                record_rejection(portfolio, "max_positions")
                continue

            equity = current_equity(portfolio, last_prices)
            if equity <= 0:
                account_halted = True
                record_rejection(portfolio, "insufficient_equity")
                continue

            if LEVERAGE < 1:
                record_rejection(portfolio, "leverage_limit")
                continue

            available = (
                available_margin(
                    portfolio,
                    last_prices,
                )
            )

            leverage_headroom = max(
                0.0,
                equity * max(LEVERAGE, 1.0) - open_notional(portfolio),
            )
            if leverage_headroom <= 0:
                record_rejection(portfolio, "leverage_limit")
                continue

            exposure_headroom = max(
                0.0,
                min(
                    allowed_exposure(portfolio, last_prices) - open_notional(portfolio),
                    leverage_headroom,
                ),
            )

            if available <= 0:
                record_rejection(portfolio, "insufficient_margin")
                continue

            if exposure_headroom <= 0:
                record_rejection(portfolio, "max_exposure")
                continue

            target_notional = (
                available *
                max(LEVERAGE, 1.0) *
                POSITION_SIZE_PCT /
                100.0
            )
            target_notional = min(target_notional, exposure_headroom)

            max_order_notional = float(getattr(SETTINGS, "max_order_notional_usdt", 0.0))
            if max_order_notional > 0:
                target_notional = min(target_notional, max_order_notional)

            if (
                target_notional <
                MIN_TRADE_USDT
            ):

                portfolio.skipped_entries += 1
                portfolio.skipped_min_trade += 1
                portfolio.rejected_entries_by_reason["insufficient_margin"] = (
                    portfolio.rejected_entries_by_reason.get("insufficient_margin", 0) + 1
                )
                continue

            price = safe_float(
                row["close"]
            )

            if price <= 0:

                record_rejection(portfolio, "risk_gate")
                continue

            qty = (
                target_notional /
                price
            )

            notional = (
                qty *
                price
            )

            if (
                qty <= 0
                or
                notional <
                MIN_TRADE_USDT
            ):

                record_rejection(portfolio, "insufficient_margin")
                continue

            entry_fee = (
                notional *
                TAKER_FEE_RATE
            )

            if (
                portfolio.cash <
                entry_fee
            ):

                record_rejection(portfolio, "insufficient_equity")
                continue

            lot = Lot(
                symbol=symbol,
                entry_ts=ts,
                entry_idx=current_idx,
                entry_price=price,
                qty=qty,
                notional=notional,
                entry_fee=entry_fee,
            )

            portfolio.cash -= (
                entry_fee
            )

            portfolio.fees_paid += (
                entry_fee
            )

            portfolio.entry_count += 1

            symbol_entries[
                symbol
            ] += 1

            open_lots[
                symbol
            ].append(lot)

            update_account_diagnostics(portfolio, last_prices)
            assert_account_invariants(portfolio, last_prices)

        # ----------------------------------------------------
        # MARK TO MARKET
        # ----------------------------------------------------

        portfolio.lots = [
            lot
            for lots in open_lots.values()
            for lot in lots
        ]

        equity = current_equity(
            portfolio,
            last_prices,
        )

        update_account_diagnostics(portfolio, last_prices)
        assert_account_invariants(portfolio, last_prices)

        portfolio.equity_curve.append(
            (
                ts,
                equity,
            )
        )

        # Realized-only: portfolio.cash already excludes unrealized
        # P&L of anything still open - it only moves on entry fees
        # (paid immediately) and on close_lot() (realized net P&L).
        portfolio.realized_equity_curve.append(
            (
                ts,
                portfolio.cash,
            )
        )

    # ========================================================
    # FINAL STATE
    # ========================================================

    portfolio.lots = [
        lot
        for lots in open_lots.values()
        for lot in lots
    ]

    ending_equity = (
        current_equity(
            portfolio,
            last_prices,
        )
    )

    update_account_diagnostics(portfolio, last_prices)
    assert_account_invariants(portfolio, last_prices)

    equity_values = [value for _, value in portfolio.equity_curve]
    peak_equity = max(equity_values, default=ending_equity)
    minimum_equity = min(equity_values, default=ending_equity)
    ending_used_margin = margin_in_use(portfolio)
    ending_open_notional = open_notional(portfolio)
    ending_available_margin = max(0.0, ending_equity - ending_used_margin)

    unrealized_net = (
        ending_equity -
        portfolio.cash
    )

    total_net_pnl = (
        ending_equity -
        portfolio.starting_capital
    )

    total_return = (
        total_net_pnl /
        portfolio.starting_capital *
        100.0
    )

    # ========================================================
    # MAX DRAWDOWN
    # ========================================================

    curve = np.array(
        [
            value
            for _, value
            in portfolio.equity_curve
        ],
        dtype=float,
    )

    if len(curve):

        peaks = np.maximum.accumulate(
            curve
        )

        drawdowns = np.where(
            peaks > 0,
            (
                (
                    peaks -
                    curve
                ) /
                peaks
            ) *
            100.0,
            0.0,
        )

        max_drawdown = float(
            np.max(
                drawdowns
            )
        )

    else:

        max_drawdown = 0.0

    # Realized-only drawdown: same peak-to-trough math, but on the
    # cash-only curve, so currently-open (not-yet-closed) positions
    # cannot contribute to this figure at all.
    realized_curve = np.array(
        [value for _, value in portfolio.realized_equity_curve],
        dtype=float,
    )

    if len(realized_curve):

        realized_peaks = np.maximum.accumulate(realized_curve)

        realized_drawdowns = np.where(
            realized_peaks > 0,
            ((realized_peaks - realized_curve) / realized_peaks) * 100.0,
            0.0,
        )

        realized_max_drawdown = float(np.max(realized_drawdowns))

    else:

        realized_max_drawdown = 0.0

    # ========================================================
    # LOSS STATS
    # ========================================================

    losing_trades = [
        t.net_pnl
        for t in portfolio.closed_trades
        if t.net_pnl <= 0
    ]

    winning_trades = [
        t.net_pnl
        for t in portfolio.closed_trades
        if t.net_pnl > 0
    ]

    avg_loss = (
        sum(losing_trades) / len(losing_trades)
        if losing_trades else 0.0
    )

    avg_win = (
        sum(winning_trades) / len(winning_trades)
        if winning_trades else 0.0
    )

    worst_trade = (
        min(t.net_pnl for t in portfolio.closed_trades)
        if portfolio.closed_trades else 0.0
    )

    best_trade = (
        max(t.net_pnl for t in portfolio.closed_trades)
        if portfolio.closed_trades else 0.0
    )

    # ========================================================
    # OPEN P&L BY SYMBOL
    # ========================================================

    symbol_open_pnl: Dict[
        str,
        float
    ] = {
        symbol: 0.0
        for symbol in frames
    }

    symbol_open_notional: Dict[str, float] = {
        symbol: 0.0
        for symbol in frames
    }

    for lot in portfolio.lots:

        price = last_prices.get(
            lot.symbol,
            lot.entry_price,
        )

        gross = (
            price -
            lot.entry_price
        ) * lot.qty

        estimated_exit_fee = (
            price *
            lot.qty *
            TAKER_FEE_RATE
        )

        symbol_open_pnl[
            lot.symbol
        ] += (
            gross -
            estimated_exit_fee
        )

        symbol_open_notional[lot.symbol] += lot.notional

    total_open_notional = sum(symbol_open_notional.values())

    # ========================================================
    # REPORTS
    # ========================================================

    reports_dir = (
        ROOT /
        "backtest_reports"
    )

    reports_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # TRADES
    # --------------------------------------------------------

    trade_rows = []

    for trade in (
        portfolio.closed_trades
    ):

        data = asdict(
            trade
        )

        data[
            "entry_time_utc"
        ] = utc_string(
            trade.entry_ts
        )

        data[
            "exit_time_utc"
        ] = utc_string(
            trade.exit_ts
        )

        trade_rows.append(
            data
        )

    pd.DataFrame(
        trade_rows
    ).to_csv(
        reports_dir /
        "ema_rsi_shared_trades.csv",
        index=False,
    )

    # --------------------------------------------------------
    # SYMBOL SUMMARY
    # --------------------------------------------------------

    summary_rows = []

    for symbol in frames:

        symbol_trades = [
            trade
            for trade
            in portfolio.closed_trades
            if trade.symbol == symbol
        ]

        wins = sum(
            1
            for trade
            in symbol_trades
            if trade.net_pnl > 0
        )

        losses = (
            len(symbol_trades) -
            wins
        )

        realized = sum(
            trade.net_pnl
            for trade
            in symbol_trades
        )

        open_net = (
            symbol_open_pnl[
                symbol
            ]
        )

        summary_rows.append(
            {
                "symbol": symbol,
                "entries":
                    symbol_entries[symbol],
                "closed_trades":
                    len(symbol_trades),
                "wins":
                    wins,
                "losses":
                    losses,
                "win_rate_pct":
                    (
                        wins /
                        len(symbol_trades) *
                        100.0
                    )
                    if symbol_trades
                    else 0.0,
                "realized_net_pnl":
                    realized,
                "open_unrealized_net_pnl":
                    open_net,
                "total_symbol_pnl":
                    realized +
                    open_net,
                "stops":
                    symbol_stops[symbol],
                "open_lots":
                    len(
                        open_lots[symbol]
                    ),
                "open_notional_usdt":
                    symbol_open_notional[symbol],
            }
        )

    summary_df = pd.DataFrame(
        summary_rows
    )

    summary_df.to_csv(
        reports_dir /
        "ema_rsi_shared_summary.csv",
        index=False,
    )

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    closed_win_rate = (
        portfolio.closed_wins /
        portfolio.closed_trades_count *
        100.0
    ) if (
        portfolio.closed_trades_count
    ) else 0.0

    report = {

        "period": {
            "days":
                args.days,
            "timeframe_minutes":
                15,
            "start_utc":
                utc_string(
                    test_start_ms
                ),
            "end_utc":
                utc_string(
                    test_end_ms
                ),
        },

        "strategy": {

            "mode":
                STRATEGY_MODE,

            "ema_fast":
                EMA_FAST,

            "ema_slow":
                EMA_SLOW,

            "ema_period":
                EMA_PERIOD,

            "rsi_period":
                RSI_PERIOD,

            "entry_rsi":
                ENTRY_RSI,

            "exit_rsi":
                EXIT_RSI,

            "cooldown_candles":
                COOLDOWN_CANDLES,

            "exit_candles":
                EXIT_CANDLES,

            "hard_stop_pct":
                STOP_LOSS_PCT,

            "enable_long":
                ENABLE_LONG,

            "enable_short":
                False,
        },

        "portfolio": {

            "starting_capital":
                args.capital,

            "starting_equity":
                args.capital,

            "ending_equity":
                ending_equity,

            "realized_net_pnl":
                portfolio.realized_pnl,

            "unrealized_net_pnl":
                unrealized_net,

            "total_net_pnl":
                total_net_pnl,

            "return_pct":
                total_return,

            "max_drawdown_pct":
                max_drawdown,

            "fees_paid":
                portfolio.fees_paid,

            "peak_equity":
                peak_equity,

            "minimum_equity":
                minimum_equity,

            "used_margin_at_end":
                ending_used_margin,

            "available_margin_at_end":
                ending_available_margin,

            "maximum_used_margin":
                portfolio.max_used_margin,

            "maximum_open_notional":
                portfolio.max_open_notional,

            "maximum_account_leverage":
                portfolio.max_account_leverage,

            "maximum_concurrent_lots":
                portfolio.max_concurrent_lots,

            "entries":
                portfolio.entry_count,

            "closed_trades":
                portfolio.closed_trades_count,

            "closed_wins":
                portfolio.closed_wins,

            "closed_losses":
                portfolio.closed_losses,

            "closed_win_rate_pct":
                closed_win_rate,

            "avg_loss_usdt":
                avg_loss,

            "avg_win_usdt":
                avg_win,

            "worst_trade_usdt":
                worst_trade,

            "best_trade_usdt":
                best_trade,

            "total_open_position_value_usdt":
                total_open_notional,

            "total_exposure_at_end":
                ending_open_notional,

            "max_drawdown_pct_realized_only":
                realized_max_drawdown,

            "max_drawdown_pct_mark_to_market":
                max_drawdown,

            "stops":
                portfolio.stop_count,

            "open_lots_at_end":
                len(
                    portfolio.lots
                ),

            "skipped_entries":
                portfolio.skipped_entries,

            "skipped_min_trade":
                portfolio.skipped_min_trade,

            "rejected_entries_by_reason":
                portfolio.rejected_entries_by_reason,
        },

        "symbols":
            symbols,
    }

    (
        reports_dir /
        "ema_rsi_shared_report.json"
    ).write_text(
        json.dumps(
            report,
            indent=2,
        ),
        encoding="utf-8",
    )

    # ========================================================
    # CONSOLE
    # ========================================================

    print("\n")

    print("=" * 120)

    print(
        "30-DAY EMA200 + RSI20 / "
        "15m SHARED-ACCOUNT RESULTS"
    )

    print("=" * 120)

    print(
        "SYMBOL       "
        "ENTRIES   "
        "CLOSED   "
        "WIN%   "
        "REALIZED NET   "
        "OPEN NET     "
        "TOTAL NET   "
        "STOPS  "
        "OPEN LOTS  "
        "OPEN VALUE"
    )

    print("-" * 120)

    for row in summary_rows:

        print(
            f"{row['symbol']:<12}"
            f"{row['entries']:>8}"
            f"{row['closed_trades']:>9}"
            f"{row['win_rate_pct']:>7.1f}%"
            f"{row['realized_net_pnl']:>15.4f}"
            f"{row['open_unrealized_net_pnl']:>12.4f}"
            f"{row['total_symbol_pnl']:>13.4f}"
            f"{row['stops']:>7}"
            f"{row['open_lots']:>10}"
            f"{row['open_notional_usdt']:>12.2f}"
        )

    print("-" * 120)

    print(
        f"TOTAL CLOSED TRADES : "
        f"{portfolio.closed_trades_count}"
    )

    print(
        f"CLOSED WIN RATE      : "
        f"{closed_win_rate:.2f}%"
    )

    print(
        f"REALIZED NET P&L     : "
        f"{portfolio.realized_pnl:.4f} USDT"
    )

    print(
        f"UNREALIZED NET P&L   : "
        f"{unrealized_net:.4f} USDT"
    )

    print(
        f"TOTAL NET P&L        : "
        f"{total_net_pnl:.4f} USDT"
    )

    print(
        f"STARTING CAPITAL     : "
        f"{args.capital:.4f} USDT"
    )

    print(
        f"ENDING EQUITY        : "
        f"{ending_equity:.4f} USDT"
    )

    print(
        f"TOTAL RETURN         : "
        f"{total_return:.2f}%"
    )

    print(
        f"TOTAL OPEN POSITION VALUE : "
        f"{total_open_notional:.4f} USDT "
        f"(notional, still open - not yet closed)"
    )

    print(
        f"MAX DRAWDOWN (REALIZED ONLY, excl. open) : "
        f"{realized_max_drawdown:.2f}%"
    )

    print(
        f"MAX DRAWDOWN (MARK-TO-MARKET, incl. open): "
        f"{max_drawdown:.2f}%"
    )

    print(
        f"CLOSED LOSSES        : "
        f"{portfolio.closed_losses}"
    )

    print(
        f"AVG LOSS             : "
        f"{avg_loss:.4f} USDT"
    )

    print(
        f"AVG WIN              : "
        f"{avg_win:.4f} USDT"
    )

    print(
        f"WORST TRADE          : "
        f"{worst_trade:.4f} USDT"
    )

    print(
        f"BEST TRADE           : "
        f"{best_trade:.4f} USDT"
    )

    print(
        f"FEES PAID            : "
        f"{portfolio.fees_paid:.4f} USDT"
    )

    print(
        f"OPEN LOTS AT END     : "
        f"{len(portfolio.lots)}"
    )

    print(
        f"STOPS                : "
        f"{portfolio.stop_count}"
    )

    print(
        f"SKIPPED ENTRIES      : "
        f"{portfolio.skipped_entries}"
    )

    print("=" * 120)

    print(
        "NO LIVE ORDERS WERE SENT."
    )

    print(
        "\nREPORT FILES:"
    )

    print(
        reports_dir /
        "ema_rsi_shared_summary.csv"
    )

    print(
        reports_dir /
        "ema_rsi_shared_trades.csv"
    )

    print(
        reports_dir /
        "ema_rsi_shared_report.json"
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()