# Contributing

## Local Setup

```bash
cd EMA-BAND
python3 -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Keep `ENABLE_LIVE_TRADING=false` and `BYBIT_TESTNET=true` during development.

## Checks Before Submission

```bash
python3 -m compileall -q .
python3 -m pytest -q
python3 backtest.py
```

Run `python3 preflight.py` before any testnet or live start. Review all warnings and failures; never use `--set-leverage` casually because it changes exchange account settings.

## Changes

Keep changes focused, avoid committing generated databases, logs, credentials, or backtest output containing sensitive data, and document configuration changes in the README or `.env.example`.
