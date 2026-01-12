import os
import asyncio
from typing import Optional

from telethon import TelegramClient, events

from .db import get_user_settings, list_positions
from .wallets import wallet_get_pubkey
from .auto_trader import auto_buy_for_user, auto_sell_for_position


def _get_bot_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required")
    return token


def _parse_buy_args(text: str) -> tuple[str, Optional[float]]:
    parts = text.split()
    if len(parts) < 2:
        raise ValueError("Usage: /buy <mint> [sol]")
    mint = parts[1].strip()
    sol = float(parts[2]) if len(parts) > 2 else None
    return mint, sol


def _parse_sell_args(text: str) -> tuple[str, float]:
    parts = text.split()
    if len(parts) < 3:
        raise ValueError("Usage: /sell <mint> <pct>")
    mint = parts[1].strip()
    pct = float(parts[2])
    if pct <= 0 or pct > 100:
        raise ValueError("pct must be in (0,100]")
    return mint, pct


async def run_bot() -> None:
    api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not api_id or not api_hash:
        raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")

    token = _get_bot_token()
    session = os.getenv("TELETHON_SESSION", "scrapetech_session").strip() + "_bot"

    client = TelegramClient(session, api_id, api_hash)
    await client.start(bot_token=token)

    @client.on(events.NewMessage(pattern=r"^/start"))
    async def _start(event):
        await event.respond(
            "Scrapetech bot online.\n"
            "Commands:\n"
            "/status\n"
            "/wallet\n"
            "/buy <mint> [sol]\n"
            "/sell <mint> <pct>\n"
            "/positions"
        )

    @client.on(events.NewMessage(pattern=r"^/status"))
    async def _status(event):
        user_id = str(event.sender_id)
        s = get_user_settings(user_id)
        await event.respond(
            f"trade_mode={s.get('trade_mode')}\n"
            f"position_mode={s.get('position_mode')}\n"
            f"buy_amount_sol={s.get('buy_amount_sol')}\n"
            f"buy_slippage_pct={s.get('buy_slippage_pct')}\n"
            f"sell_slippage_pct={s.get('sell_slippage_pct')}\n"
            f"tp_sl_enabled={s.get('tp_sl_enabled')}\n"
            f"take_profit_pct={s.get('take_profit_pct')}\n"
            f"stop_loss_pct={s.get('stop_loss_pct')}"
        )

    @client.on(events.NewMessage(pattern=r"^/wallet"))
    async def _wallet(event):
        user_id = str(event.sender_id)
        pub = wallet_get_pubkey(user_id)
        if not pub:
            await event.respond("No wallet found. Use the CLI to create/import.")
            return
        await event.respond(f"wallet={pub}")

    @client.on(events.NewMessage(pattern=r"^/positions"))
    async def _positions(event):
        user_id = str(event.sender_id)
        rows = list_positions(user_id)
        if not rows:
            await event.respond("No positions.")
            return
        lines = []
        for r in rows:
            lines.append(
                f"{r['mint']} | tokens={r['token_balance']} | avg_entry={r['avg_entry_sol']} | "
                f"pnl={r['realized_pnl_sol']} | open={r['open']}"
            )
        await event.respond("\n".join(lines))

    @client.on(events.NewMessage(pattern=r"^/buy"))
    async def _buy(event):
        user_id = str(event.sender_id)
        try:
            mint, sol = _parse_buy_args(event.raw_text)
        except Exception as e:
            await event.respond(str(e))
            return

        await event.respond("Submitting buy...")
        sig = await asyncio.to_thread(auto_buy_for_user, user_id, mint, sol)
        await event.respond(f"Buy submitted: {sig}")

    @client.on(events.NewMessage(pattern=r"^/sell"))
    async def _sell(event):
        user_id = str(event.sender_id)
        try:
            mint, pct = _parse_sell_args(event.raw_text)
        except Exception as e:
            await event.respond(str(e))
            return

        pos = list_positions(user_id)
        row = next((r for r in pos if r["mint"] == mint), None)
        if not row or float(row["token_balance"]) <= 0:
            await event.respond("No position balance found.")
            return

        tokens = float(row["token_balance"]) * (pct / 100.0)
        await event.respond("Submitting sell...")
        sig = await asyncio.to_thread(auto_sell_for_position, user_id, mint, tokens)
        await event.respond(f"Sell submitted: {sig}")

    await client.run_until_disconnected()
