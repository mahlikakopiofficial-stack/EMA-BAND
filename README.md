# EMA-BAND

Long-only EMA trading bot for Bybit linear contracts, with an optional EMA + RSI strategy, Telegram notifications, and a localhost dashboard.

## Security First

The bot can place real orders. Start in paper mode or Bybit testnet, never enable withdrawals on the API key, and set an order-notional cap before using live trading.

The existing `.env` file is private runtime configuration and must never be committed. Use `.env.example` as a template, then run:

```bash
chmod 600 .env
```

If credentials have ever been exposed, revoke and regenerate both the Bybit API key and Telegram bot token before continuing.

## Requirements

- Python 3.11 or newer
- Bybit account for testnet or live operation
- Optional Telegram bot for notifications

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

## Configuration

```bash
cp .env.example .env
chmod 600 .env
```

At minimum, review `BYBIT_TESTNET`, `ENABLE_LIVE_TRADING`, `MAX_ORDER_NOTIONAL_USDT`, `MAX_LONG_ENTRIES`, `MAX_TOTAL_OPEN_LOTS`, and `MAX_DAILY_LOSS_USDT`. Keep `DASHBOARD_HOST=127.0.0.1`.

The bot uses Bybit's isolated-margin, One-Way position model with the configured
leverage. Entries on the same symbol are merged by Bybit into one position.
`MAX_LONG_ENTRIES` limits the number of open lots per symbol (default: `5`),
`MAX_TOTAL_OPEN_LOTS` limits total open lots across all symbols (default: `15`;
set to `0` to disable), and `MAX_ACCOUNT_EXPOSURE_USDT` remains an independent
account-wide cap. Telegram runs the same backtest script with the bot's
configuration; each report prints the effective global lot cap. To compare
against a terminal run, run it from the same installation and configuration.

The default emergency exit is liquidation-aware: when a merged position reaches
the configured `LIQUIDATION_BUFFER_PCT` of the distance from its weighted
average entry to Bybit's live liquidation price, the bot closes the entire
merged symbol position in one reduce-only market order. In backtests, the
liquidation price is estimated using `LEVERAGE` and
`MAINTENANCE_MARGIN_RATE` (default: `0.005`).

`STOP_LOSS_PCT` remains parseable for backward-compatible configuration files,
but is deprecated and is not used to decide exits.

## Preflight

Run this before starting the bot:

```bash
python3 preflight.py
```

Preflight may contact Bybit and send a Telegram test message when live credentials are configured. It does not place orders unless `--set-leverage` is explicitly supplied, but review its output before proceeding. Use `--set-leverage` intentionally when you want preflight to enforce the configured isolated leverage:

```bash
python3 preflight.py --set-leverage
```

## Run

```bash
python3 main.py
```

The dashboard is available at <http://127.0.0.1:8080> by default. Do not expose this port publicly.

When Telegram credentials are configured, commands from the configured chat are supported:

```text
/start   allow new entries
/stop    block new entries; existing positions remain managed
/status  show mode, gate state, and open lots
/health  show engine and websocket health
/positions show open positions
/pnl     show account and daily PnL
/trades  show the five most recent trades
/pending show pending orders
/alerts  show recent execution alerts
/help    show available commands
```

Telegram commands control the trading gate only. They cannot start a stopped VPS process; use `systemd` for process recovery. Messages queued before startup are ignored.

To stop new entries without stopping the process:

```bash
touch EMERGENCY_STOP
```

Remove the file only after reviewing the account and logs.

## VPS systemd Deployment

The repository includes a hardened service template at `deploy/ema-band.service`. It runs as a dedicated non-root user, restarts after process failure, and allows writes only to `data/` and `logs/`.

On the VPS, use an installation such as:

```bash
sudo useradd --system --home /opt/ema-band --shell /usr/sbin/nologin emabot
sudo mkdir -p /opt/ema-band
sudo chown -R emabot:emabot /opt/ema-band
sudo cp deploy/ema-band.service /etc/systemd/system/ema-band.service
sudo systemctl daemon-reload
sudo systemctl enable --now ema-band
sudo systemctl status ema-band
sudo journalctl -u ema-band -f
```

Before enabling it, copy the application to `/opt/ema-band/EMA-BAND`, create `/opt/ema-band/.venv`, create the private `.env`, and run `chmod 600 /opt/ema-band/EMA-BAND/.env`. Access the dashboard through an SSH tunnel instead of opening port 8080 in the VPS firewall:

```bash
ssh -N -L 8080:127.0.0.1:8080 user@your-vps
```

## Backtesting

```bash
python3 backtest.py
```

Backtest results are printed to the console; no report files are saved.
The backtest uses the same merged weighted-average entry, liquidation-buffer
formula, strategy-exit arming, per-symbol lot cap, cooldown, and percentage of
available balance for sizing as the live engine. Leverage limits modeled
margin/exposure and estimated liquidation; it is not multiplied into the
position-size percentage. The backtest uses simulated available margin rather
than Bybit's live wallet response, and cannot exactly reproduce Bybit quantity
steps/minimums, fills, funding, or slippage. Treat its results as a historical
strategy test, not a guarantee of live profitability. A short validation run
can be limited to one symbol:

The reported closed-trade win rate excludes positions still open. Since the
strategy holds RSI-triggered exits until they are net-profitable (unless the
liquidation buffer triggers), review total net P&L, open P&L, open notional,
and mark-to-market drawdown rather than relying on win rate alone.

```bash
python3 backtest.py --days 1 --symbol BTCUSDT
```

## Strategies

- `ema_band`: enters long when the 15-minute close is between the configured EMA band; exits only when the configured candle, RSI, and profitability conditions are met.
- `ema_rsi`: enters long above the configured EMA when RSI is below the entry threshold; exits when RSI reaches the exit threshold and the trade is profitable.

Set `STRATEGY_MODE` in `.env` to select a strategy.

## Project Layout

```text
config.py                 Environment-backed settings and validation
main.py                   Trading engine and dashboard entry point
preflight.py              Configuration, connectivity, and risk checks
backtest.py               Historical strategy backtesting
core/                     Exchange, engine, risk, persistence, and messaging code
strategies/               Trading strategy implementations
web/                      Local Flask dashboard
data/                     Local SQLite runtime data
logs/                     Runtime logs
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).

Trading software involves financial risk. This project is provided as-is and is not financial advice.
