# Security Policy

## Scope

This repository contains software that can connect to Bybit and place leveraged trades. Treat API credentials, Telegram tokens, account data, database files, and logs as sensitive.

## Reporting a Vulnerability

Do not open a public issue containing credentials, private data, or an exploitable detail. Contact the repository owner privately with:

- A short description of the issue
- Affected file or component
- Reproduction steps
- Potential impact
- A suggested mitigation, if available

## Credential Handling

- Never commit `.env`, API keys, Telegram tokens, database files, or logs.
- Create Bybit keys with contract-trading permissions only; disable withdrawals.
- Prefer Bybit testnet and paper mode while developing.
- Rotate credentials immediately if they appear in a commit, terminal transcript, screenshot, log, or chat.
- Keep `.env` owner-readable only: `chmod 600 .env`.
- Keep the dashboard bound to `127.0.0.1` and do not expose port `8080` publicly.

## Emergency Stop

Create `EMERGENCY_STOP` beside `config.py` to block new entries. Existing positions are not automatically closed, so inspect the account and manage open positions separately.
