# Scrapetech

Telegram channel scraper + Solana auto trader.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Environment

Set these in `.env`:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_BOT_TOKEN` (for the bot UI)
- `TELETHON_SESSION` (session name; defaults to `scrapetech_session`)
- `SCRAPETECH_DB` (optional; defaults to `scrapetech.db`)
- `SCRAPETECH_WALLET_PASSWORD`
- `SOLANA_RPC_URL`
- `TEST_CHANNEL` (optional)

## Run

Listener (channel scraper + auto-buy):
```bash
python -m scrapetech listen --channel @yourchannel
```

Bot (user commands):
```bash
python -m scrapetech bot
```

Reconcile pending trades:
```bash
python -m scrapetech reconcile --watch --interval 10
```

Monitor positions for TP/SL:
```bash
python -m scrapetech monitor --interval 10
```

## Notes

- Auto-buy requires the user to have a wallet and an ACTIVE subscription.
- Positions and trades are stored in the SQLite DB defined by `SCRAPETECH_DB`.
- For production, run listener, reconcile, and monitor as long-running services.
