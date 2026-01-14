#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  dotenv=""
  if [[ -f ".env" ]]; then
    dotenv=".env"
  elif [[ -f "../.env" ]]; then
    dotenv="../.env"
  fi

  if [[ -n "${dotenv}" ]]; then
    token_line="$(grep -E '^TELEGRAM_BOT_TOKEN=' "${dotenv}" | tail -n 1 || true)"
    if [[ -n "${token_line}" ]]; then
      token="${token_line#TELEGRAM_BOT_TOKEN=}"
      token="${token%\"}"
      token="${token#\"}"
      export TELEGRAM_BOT_TOKEN="${token}"
    fi
  fi
fi

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  echo "TELEGRAM_BOT_TOKEN is not set (export it or add to .env)"
  exit 1
fi

payload='[
  {"command":"start","description":"Open the main menu"},
  {"command":"wallet","description":"Show your wallet"},
  {"command":"positions","description":"Show positions"},
  {"command":"buy","description":"Buy by mint"},
  {"command":"sell","description":"Sell by mint"},
  {"command":"help","description":"How to use the bot"}
]'

curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setMyCommands" \
  -d "commands=${payload}"
echo
