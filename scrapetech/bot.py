import os
import asyncio
from typing import Optional

from telethon import TelegramClient, events, Button

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


def _main_menu():
    return [
        [Button.inline("Wallet", b"menu:wallet"), Button.inline("Positions", b"menu:positions")],
        [Button.inline("Buy", b"menu:buy"), Button.inline("Sell", b"menu:sell")],
        [Button.inline("Settings", b"menu:settings"), Button.inline("Help", b"menu:help")],
    ]


def _sell_presets(mint: str):
    return [
        [
            Button.inline("Sell 10%", f"sell:{mint}:10".encode("utf-8")),
            Button.inline("Sell 25%", f"sell:{mint}:25".encode("utf-8")),
        ],
        [
            Button.inline("Sell 50%", f"sell:{mint}:50".encode("utf-8")),
            Button.inline("Sell 100%", f"sell:{mint}:100".encode("utf-8")),
        ],
        [Button.inline("Back", b"menu:main")],
    ]


async def run_bot() -> None:
    api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not api_id or not api_hash:
        raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")

    token = _get_bot_token()
    session = os.getenv("TELETHON_SESSION", "scrapetech_session").strip() + "_bot"

    client = TelegramClient(session, api_id, api_hash)
    await client.start(bot_token=token)

    pending = {}

    @client.on(events.NewMessage(pattern=r"^/start"))
    async def _start(event):
        await event.respond(
            "Scrapetech bot online. Use the menu below.",
            buttons=_main_menu(),
        )

    @client.on(events.NewMessage(pattern=r"^/menu"))
    async def _menu(event):
        await event.respond("Main menu:", buttons=_main_menu())

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

    @client.on(events.NewMessage)
    async def _text_router(event):
        user_id = str(event.sender_id)
        state = pending.get(user_id)
        if not state:
            return
        if state.get("mode") == "buy":
            try:
                mint, sol = _parse_buy_args(event.raw_text)
            except Exception as e:
                await event.respond(str(e))
                return
            pending.pop(user_id, None)
            await event.respond("Submitting buy...")
            sig = await asyncio.to_thread(auto_buy_for_user, user_id, mint, sol)
            await event.respond(f"Buy submitted: {sig}")
            return

        if state.get("mode") == "sell":
            try:
                mint, pct = _parse_sell_args(event.raw_text)
            except Exception as e:
                await event.respond(str(e))
                return
            pending.pop(user_id, None)
            pos = list_positions(user_id)
            row = next((r for r in pos if r["mint"] == mint), None)
            if not row or float(row["token_balance"]) <= 0:
                await event.respond("No position balance found.")
                return
            tokens = float(row["token_balance"]) * (pct / 100.0)
            await event.respond("Submitting sell...")
            sig = await asyncio.to_thread(auto_sell_for_position, user_id, mint, tokens)
            await event.respond(f"Sell submitted: {sig}")
            return

    @client.on(events.CallbackQuery)
    async def _callbacks(event):
        user_id = str(event.sender_id)
        data = event.data.decode("utf-8")

        if data == "menu:main":
            await event.edit("Main menu:", buttons=_main_menu())
            return
        if data == "menu:wallet":
            pub = wallet_get_pubkey(user_id)
            text = f"wallet={pub}" if pub else "No wallet found. Use the CLI to create/import."
            await event.edit(text, buttons=_main_menu())
            return
        if data == "menu:positions":
            rows = list_positions(user_id)
            if not rows:
                await event.edit("No positions.", buttons=_main_menu())
                return
            lines = []
            for r in rows:
                lines.append(
                    f"{r['mint']} | tokens={r['token_balance']} | avg_entry={r['avg_entry_sol']} | "
                    f"pnl={r['realized_pnl_sol']} | open={r['open']}"
                )
            await event.edit("\n".join(lines), buttons=_main_menu())
            return
        if data == "menu:settings":
            s = get_user_settings(user_id)
            await event.edit(
                f"buy_amount_sol={s.get('buy_amount_sol')}\n"
                f"buy_slippage_pct={s.get('buy_slippage_pct')}\n"
                f"sell_slippage_pct={s.get('sell_slippage_pct')}\n"
                f"tp_sl_enabled={s.get('tp_sl_enabled')}\n"
                f"take_profit_pct={s.get('take_profit_pct')}\n"
                f"stop_loss_pct={s.get('stop_loss_pct')}",
                buttons=_main_menu(),
            )
            return
        if data == "menu:help":
            await event.edit(
                "Commands:\n"
                "/buy <mint> [sol]\n"
                "/sell <mint> <pct>\n"
                "/positions\n"
                "/status\n"
                "/wallet",
                buttons=_main_menu(),
            )
            return
        if data == "menu:buy":
            pending[user_id] = {"mode": "buy"}
            await event.edit("Send: /buy <mint> [sol]", buttons=_main_menu())
            return
        if data == "menu:sell":
            rows = list_positions(user_id)
            open_rows = [r for r in rows if float(r["token_balance"]) > 0]
            if not open_rows:
                await event.edit("No positions to sell.", buttons=_main_menu())
                return
            if len(open_rows) == 1:
                mint = open_rows[0]["mint"]
                await event.edit(f"Sell presets for {mint}:", buttons=_sell_presets(mint))
                return
            buttons = [[Button.inline(r["mint"][:8], f"sellpick:{r['mint']}".encode("utf-8"))] for r in open_rows]
            buttons.append([Button.inline("Back", b"menu:main")])
            await event.edit("Select a mint:", buttons=buttons)
            return

        if data.startswith("sellpick:"):
            mint = data.split(":", 1)[1]
            await event.edit(f"Sell presets for {mint}:", buttons=_sell_presets(mint))
            return
        if data.startswith("sell:"):
            _tag, mint, pct_s = data.split(":")
            pct = float(pct_s)
            pos = list_positions(user_id)
            row = next((r for r in pos if r["mint"] == mint), None)
            if not row or float(row["token_balance"]) <= 0:
                await event.edit("No position balance found.", buttons=_main_menu())
                return
            tokens = float(row["token_balance"]) * (pct / 100.0)
            await event.edit("Submitting sell...", buttons=_main_menu())
            sig = await asyncio.to_thread(auto_sell_for_position, user_id, mint, tokens)
            await event.respond(f"Sell submitted: {sig}")
            return

    await client.run_until_disconnected()
