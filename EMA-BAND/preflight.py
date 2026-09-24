#!/usr/bin/env python3
"""
PRE-FLIGHT CHECK — run this before every live start.

    python3 preflight.py

It reads your real .env, shows exactly what the bot WILL do with those settings,
and checks connectivity, credentials, leverage, position mode and Telegram.
It places NO orders and changes nothing on your account (except setting leverage,
which it only does with --set-leverage).

Exit code 0 = safe to start. Non-zero = fix the FAILs first.
"""
from __future__ import annotations
import sys, os, argparse, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

OK, WARN, FAIL = "PASS", "WARN", "FAIL"
results = []


def check(name, status, detail=""):
    results.append((name, status, detail))
    tag = {"PASS": "[ PASS ]", "WARN": "[ WARN ]", "FAIL": "[ FAIL ]"}[status]
    print(f"  {tag} {name}" + (f"\n           {detail}" if detail else ""))


def section(t):
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set-leverage", action="store_true",
                    help="actually set leverage on Bybit (otherwise it is only read/reported)")
    args = ap.parse_args()

    # ---------- 1. CONFIG LOADS ----------
    section("1. CONFIGURATION")
    try:
        from config import SETTINGS as S, check_env_permissions
    except Exception as e:
        print(f"  [ FAIL ] config.py failed to load: {e}")
        sys.exit(2)

    errs = S.validate()
    if errs:
        check("config validation", FAIL, "; ".join(errs))
    else:
        check("config validation", OK)

    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        check(".env file present", FAIL, f"{env_path} not found — copy .env.example to .env")
    else:
        check(".env file present", OK, str(env_path))
        if os.name != "nt":
            import stat
            mode = env_path.stat().st_mode
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                check(".env permissions", WARN,
                      f"mode {oct(mode)[-3:]} — readable by others. Run: chmod 600 {env_path}")
            else:
                check(".env permissions", OK, "600 (owner only)")

    # ---------- 2. WHAT THE BOT WILL DO ----------
    section("2. EFFECTIVE SETTINGS — this is exactly what will run")
    mode = ("LIVE MAINNET (REAL MONEY)" if S.enable_live_trading and not S.bybit_testnet
            else "LIVE TESTNET (fake money)" if S.enable_live_trading
            else "PAPER (no orders sent)")
    print(f"  MODE                     : {mode}")
    print(f"  STRATEGY_MODE            : {S.strategy_mode}")
    if S.strategy_mode == "ema_band":
        print(f"    entry                  : 15m close between EMA{S.ema_band_fast} and EMA{S.ema_band_slow}")
        print(f"    cooldown               : {S.ema_band_cooldown_candles} candles after each entry")
        print(f"    exit                   : after {S.ema_band_exit_candles} candles AND RSI{S.ema_band_rsi_period} >= {S.ema_band_exit_rsi:g}, only if profitable")
    else:
        print(f"    entry                  : 15m close > EMA{S.ema_rsi_ema_period} AND RSI{S.ema_rsi_period} < {S.ema_rsi_entry_rsi:g}")
        print(f"    cooldown               : {S.ema_rsi_cooldown_candles} candles")
        print(f"    exit                   : RSI{S.ema_rsi_period} >= {S.ema_rsi_exit_rsi:g}, only if profitable")
    print(f"  SYMBOLS ({len(S.symbols):>2})             : {', '.join(S.symbols)}")
    print(f"  LEVERAGE                 : {S.leverage}x")
    print(f"  STOP_LOSS_PCT            : {S.stop_loss_pct:g}%  (hard force-close, overrides strategy exit)")
    print(f"  POSITION_SIZE_PCT        : {S.position_size_pct:g}% of available balance per entry")
    print(f"  MIN_TRADE_USDT           : {S.min_trade_usdt:g}")
    print(f"  MAX_LONG_ENTRIES         : {S.max_long_entries} per symbol")
    print(f"  MAX_TOTAL_OPEN_LOTS      : {S.max_total_open_lots or 'DISABLED'} across all symbols")
    print(f"  MAX_ORDER_NOTIONAL_USDT  : {S.max_order_notional_usdt or 'DISABLED'}")
    print(f"  MAX_DAILY_LOSS_USDT      : {S.max_daily_loss_usdt or 'DISABLED'}")
    print(f"  SCANNER_INTERVAL_SECONDS : {S.scanner_interval_seconds:g}s")
    print(f"  KILL SWITCH FILE         : {S.log_dir.parent / S.kill_switch_file}")

    worst = S.max_long_entries * len(S.symbols)
    if S.max_total_open_lots:
        worst = min(worst, S.max_total_open_lots)
    print(f"\n  >> Worst-case concurrent positions : {worst}")
    print(f"  >> Worst-case notional at risk     : ~{worst * max(S.min_trade_usdt, 0):.2f} USDT minimum"
          f" (more if POSITION_SIZE_PCT sizes above the minimum)")

    if not S.max_total_open_lots:
        check("global position cap", WARN,
              f"MAX_TOTAL_OPEN_LOTS is disabled — you could hold {S.max_long_entries * len(S.symbols)} positions at once")
    else:
        check("global position cap", OK, f"{S.max_total_open_lots}")
    if not S.max_daily_loss_usdt:
        check("daily loss breaker", WARN, "MAX_DAILY_LOSS_USDT disabled — no daily loss limit")
    else:
        check("daily loss breaker", OK, f"{S.max_daily_loss_usdt} USDT")
    if not S.max_order_notional_usdt:
        check("order notional cap", WARN, "MAX_ORDER_NOTIONAL_USDT disabled — no per-order ceiling")
    else:
        check("order notional cap", OK, f"{S.max_order_notional_usdt} USDT")
    if S.enable_live_trading and not S.bybit_testnet:
        check("LIVE MAINNET", WARN, "REAL MONEY. Confirm you have run testnet first.")

    # ---------- 3. KILL SWITCH ----------
    section("3. KILL SWITCH")
    kp = S.log_dir.parent / S.kill_switch_file
    if kp.exists():
        check("kill switch", FAIL, f"{kp} EXISTS — all new entries will be blocked. Delete it to trade.")
    else:
        check("kill switch", OK, "not active")

    # ---------- 4. CREDENTIALS / CONNECTIVITY ----------
    section("4. BYBIT CONNECTIVITY")
    if not S.enable_live_trading:
        check("credentials", WARN, "ENABLE_LIVE_TRADING=false — paper mode, credentials not required")
        adapter = None
    elif not S.bybit_api_key or not S.bybit_api_secret:
        check("credentials", FAIL, "live trading enabled but BYBIT_API_KEY/SECRET missing")
        adapter = None
    else:
        check("credentials present", OK, f"key ...{S.bybit_api_key[-4:]}")
        try:
            from core.bybit import BybitAdapter
            adapter = BybitAdapter(S)
            eq, avail = adapter.get_balance()
            check("API auth + balance", OK, f"equity {eq:.4f} USDT, available {avail:.4f} USDT")
            if avail < S.min_trade_usdt:
                check("balance sufficient", FAIL,
                      f"available {avail:.4f} < MIN_TRADE_USDT {S.min_trade_usdt:g}")
            else:
                check("balance sufficient", OK)
        except Exception as e:
            check("API auth", FAIL, f"{e}")
            adapter = None

    # ---------- 5. SYMBOLS / POSITION MODE / LEVERAGE ----------
    section("5. SYMBOLS, POSITION MODE, LEVERAGE")
    if adapter:
        try:
            adapter.load_instruments()
            check("all symbols tradeable", OK, f"{len(S.symbols)} instruments loaded")
        except Exception as e:
            check("all symbols tradeable", FAIL, f"{e}")

        # position mode: the bot sends positionIdx=0 (One-Way). Hedge mode rejects every order.
        try:
            pos = adapter.get_positions()
            hedge = [p.get("symbol") for p in pos if str(p.get("positionIdx")) in ("1", "2")]
            if hedge:
                check("position mode One-Way", FAIL,
                      f"HEDGE mode detected on: {', '.join(sorted(set(hedge)))}. "
                      "The bot sends positionIdx=0 and every order will be REJECTED. "
                      "Set Position Mode to One-Way on Bybit.")
            else:
                check("position mode One-Way", OK,
                      "no hedge-mode positions seen (note: only observable where a position exists — "
                      "verify manually in Bybit settings)")
            open_now = [(p.get("symbol"), p.get("size")) for p in pos if float(p.get("size") or 0) > 0]
            if open_now:
                check("existing positions", WARN,
                      f"already open on Bybit: {open_now} — the bot will reconcile these at startup")
            else:
                check("existing positions", OK, "none")
        except Exception as e:
            check("position check", WARN, f"could not read positions: {e}")

        # leverage
        for sym in S.symbols:
            if args.set_leverage:
                try:
                    adapter.set_leverage(sym, S.leverage)
                    check(f"leverage {sym}", OK, f"set to {S.leverage}x")
                except Exception as e:
                    check(f"leverage {sym}", FAIL, f"{e}")
            else:
                pass
        if not args.set_leverage:
            check("leverage", WARN,
                  f"not verified — re-run with --set-leverage to apply {S.leverage}x, "
                  "or set it manually in Bybit. REQUIRE_LEVERAGE_CONFIRMATION="
                  f"{S.require_leverage_confirmation} means unset symbols are blocked at runtime.")
    else:
        check("symbol/leverage checks", WARN, "skipped (no live adapter)")

    # ---------- 6. TELEGRAM ----------
    section("6. TELEGRAM NOTIFICATIONS")
    from core.telegram import Telegram
    tg = Telegram(S.telegram_bot_token, S.telegram_chat_id)
    if not tg.enabled:
        check("telegram configured", WARN,
              "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — you will get NO alerts")
    else:
        sent = tg.send(
            "PRE-FLIGHT CHECK\n"
            f"Mode: {mode}\n"
            f"Strategy: {S.strategy_mode}\n"
            f"Symbols: {len(S.symbols)}\n"
            f"Leverage: {S.leverage}x | SL: {S.stop_loss_pct:g}%\n"
            "If you can read this, alerts are working."
        )
        if sent:
            check("telegram send", OK, "test message delivered — check your phone")
        else:
            check("telegram send", FAIL,
                  "send failed. Check token/chat id, and make sure you have messaged your bot once.")
        print("\n  You will receive alerts for:")
        for line in [
            "entry filled / paper entry",
            "trade closed (with PnL and reason)",
            "HARD STOP LOSS triggered",
            f"hourly PnL report ({'ON' if S.telegram_hourly_pnl else 'OFF'}, every {S.telegram_pnl_interval_seconds}s)",
            f"bot start / stop ({'ON' if S.telegram_notify_start_stop else 'OFF'})",
            "kill switch activated / cleared",
            "max daily loss reached",
            "leverage could not be set (symbol blocked)",
            "entry submission uncertain",
        ]:
            print(f"    - {line}")

    # ---------- SUMMARY ----------
    section("SUMMARY")
    f = sum(1 for _, s, _ in results if s == FAIL)
    w = sum(1 for _, s, _ in results if s == WARN)
    p = sum(1 for _, s, _ in results if s == OK)
    print(f"  PASS {p}   WARN {w}   FAIL {f}")
    if f:
        print("\n  DO NOT START. Fix the FAIL items above first.")
        return 1
    if w:
        print("\n  Safe to start, but review the WARN items — several are real risk settings")
        print("  that are disabled by default.")
        return 0
    print("\n  All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

