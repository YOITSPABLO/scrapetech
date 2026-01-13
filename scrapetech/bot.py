import os
import asyncio
from typing import Optional

from telethon import TelegramClient, events, Button
from telethon.errors import MessageNotModifiedError

from .db import get_user_settings, list_positions, update_user_settings, list_subscriptions, upsert_subscription
from .wallets import wallet_get_pubkey, wallet_create, wallet_import
from .auto_trader import auto_buy_for_user, auto_sell_for_position
from .detector import detect_mints
from .pump_quotes import quote_buy_pumpfun
from .solana_rpc import fetch_mint_info


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
        [Button.inline("Settings", b"menu:settings"), Button.inline("Channels", b"menu:channels")],
        [Button.inline("Help", b"menu:help")],
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

def _wallet_menu():
    return [
        [Button.inline("Generate Wallet", b"wallet:generate")],
        [Button.inline("Import Wallet", b"wallet:import")],
        [Button.inline("Back", b"menu:main")],
    ]

def _buy_amount_presets(mint: str):
    return [
        [
            Button.inline("0.25 SOL", f"buyamt:{mint}:0.25".encode("utf-8")),
            Button.inline("0.5 SOL", f"buyamt:{mint}:0.5".encode("utf-8")),
        ],
        [
            Button.inline("1 SOL", f"buyamt:{mint}:1".encode("utf-8")),
            Button.inline("2 SOL", f"buyamt:{mint}:2".encode("utf-8")),
        ],
        [
            Button.inline("Custom", f"buyamt:{mint}:custom".encode("utf-8")),
        ],
        [Button.inline("Back", b"menu:main")],
    ]

def _confirm_buttons(tag: str):
    return [
        [Button.inline("Confirm", f"confirm:{tag}".encode("utf-8"))],
        [Button.inline("Cancel", b"menu:main")],
    ]

def _retry_buy_buttons(mint: str, sol: float):
    return [
        [Button.inline("Retry (Buy)", f"retry_buy:{mint}:{sol}".encode("utf-8"))],
        [Button.inline("Main Menu", b"menu:main")],
    ]

def _settings_menu(s):
    buy_amt = s.get("buy_amount_sol")
    buy_slip = s.get("buy_slippage_pct")
    sell_slip = s.get("sell_slippage_pct")
    gas_fee = s.get("gas_fee_sol")
    tp_on = int(s.get("tp_sl_enabled", 1))
    tp = s.get("take_profit_pct")
    sl = s.get("stop_loss_pct")
    auto_buy = int(s.get("auto_buy_enabled", 1))
    dup_block = int(s.get("duplicate_mint_block", 1))

    tp_label = "TP/SL ✅" if tp_on else "TP/SL ❌"
    auto_label = "Auto Buy ✅" if auto_buy else "Auto Buy ❌"
    dup_label = "Duplicate Buy ⛔" if dup_block else "Duplicate Buy ✅"

    return [
        [Button.inline(f"Buy Amount | {buy_amt} SOL", b"set:buy_amount")],
        [
            Button.inline(f"Buy Slippage | {buy_slip}%", b"set:buy_slippage"),
            Button.inline(f"Sell Slippage | {sell_slip}%", b"set:sell_slippage"),
        ],
        [Button.inline(f"Gas Fee | {gas_fee} SOL", b"set:gas_fee")],
        [Button.inline(tp_label, b"set:tp_sl_toggle")],
        [
            Button.inline(f"Take Profit | {tp}%", b"set:take_profit"),
            Button.inline(f"Stop Loss | {sl}%", b"set:stop_loss"),
        ],
        [Button.inline(auto_label, b"set:auto_buy_toggle"), Button.inline(dup_label, b"set:dup_toggle")],
        [Button.inline("Scraper Settings", b"menu:channels")],
        [Button.inline("Back", b"menu:main")],
    ]

def _channels_menu():
    return [
        [Button.inline("List Channels", b"channels:list")],
        [Button.inline("Add Channel", b"channels:add"), Button.inline("Remove Channel", b"channels:remove")],
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
    last_mint = {}

    async def _safe_edit(event, text, buttons=None):
        try:
            await event.edit(text, buttons=buttons)
        except MessageNotModifiedError:
            await event.respond(text, buttons=buttons)

    @client.on(events.NewMessage(pattern=r"^/start"))
    async def _start(event):
        await event.respond(
            "Scrapetech bot online. Use the menu below.",
            buttons=_main_menu(),
        )

    @client.on(events.NewMessage(pattern=r"^/menu"))
    async def _menu(event):
        await event.respond("Main menu:", buttons=_main_menu())

    @client.on(events.NewMessage(pattern=r"^/cancel"))
    async def _cancel(event):
        user_id = str(event.sender_id)
        pending.pop(user_id, None)
        await event.respond("Canceled. Back to main menu.", buttons=_main_menu())

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
            await event.respond("No wallet found.", buttons=_wallet_menu())
            return
        await event.respond(f"wallet={pub}", buttons=_wallet_menu())

    @client.on(events.NewMessage(pattern=r"^/import"))
    async def _import(event):
        user_id = str(event.sender_id)
        parts = event.raw_text.split(maxsplit=1)
        if len(parts) < 2:
            await event.respond("Usage: /import <secret>")
            return
        secret = parts[1].strip()
        try:
            rec = wallet_import(user_id, secret)
            await event.respond(f"WALLET OK: {rec.pubkey}", buttons=_wallet_menu())
        except Exception as e:
            await event.respond(f"Import failed: {e}", buttons=_wallet_menu())

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
        try:
            sig = await asyncio.to_thread(auto_buy_for_user, user_id, mint, sol)
            await event.respond(f"Buy submitted: {sig}")
        except Exception as e:
            sol_in = sol if sol is not None else float(get_user_settings(user_id).get("buy_amount_sol") or 0.0)
            await event.respond(
                f"Buy failed.\nError: {e}",
                buttons=_retry_buy_buttons(mint, sol_in),
            )

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
        text = (event.raw_text or "").strip()
        if text.startswith("/"):
            return
        state = pending.get(user_id)
        if not state:
            # detect mints in free text and show quick trade menu
            mints = detect_mints(text)
            if not mints:
                return
            mint = mints[0].mint
            await _send_mint_card(event, user_id, mint)
            return
        prompt_id = state.get("prompt_id")
        if prompt_id and event.message.reply_to_msg_id != prompt_id:
            # allow mint detection even if waiting for a reply
            mints = detect_mints(text)
            if mints:
                pending.pop(user_id, None)
                await _send_mint_card(event, user_id, mints[0].mint)
            return
        if state.get("mode") == "buy":
            try:
                mint, sol = _parse_buy_args(event.raw_text)
            except Exception as e:
                await event.respond(str(e))
                return
            pending.pop(user_id, None)
            await event.respond("Submitting buy...")
            try:
                sig = await asyncio.to_thread(auto_buy_for_user, user_id, mint, sol)
                await event.respond(f"Buy submitted: {sig}")
            except Exception as e:
                sol_in = sol if sol is not None else float(get_user_settings(user_id).get("buy_amount_sol") or 0.0)
                await event.respond(
                    f"Buy failed.\nError: {e}",
                    buttons=_retry_buy_buttons(mint, sol_in),
                )
            return

        if state.get("mode") == "buy_mint":
            mint = event.raw_text.strip()
            if not mint:
                await event.respond("Reply with a mint address.")
                return
            pending[user_id] = {"mode": "buy_amount", "mint": mint}
            await event.respond(f"Select buy amount for {mint}:", buttons=_buy_amount_presets(mint))
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

        if state.get("mode") == "import_wallet":
            secret = event.raw_text.strip()
            if not secret:
                await event.respond("Send the secret key or seed to import.")
                return
            pending.pop(user_id, None)
            try:
                rec = wallet_import(user_id, secret)
                await event.respond(f"WALLET OK: {rec.pubkey}", buttons=_wallet_menu())
            except Exception as e:
                await event.respond(f"Import failed: {e}", buttons=_wallet_menu())
            return

        if state.get("mode") == "buy_amount_custom":
            mint = state.get("mint")
            try:
                sol = float(event.raw_text.strip())
            except Exception:
                await event.respond("Send a valid SOL amount (e.g., 0.001).")
                return
            pending.pop(user_id, None)
            await event.respond(
                f"Confirm buy:\nMINT={mint}\nSOL={sol}",
                buttons=_confirm_buttons(f"buy:{mint}:{sol}"),
            )
            return

        if state.get("mode") == "sell_pct_custom":
            mint = state.get("mint")
            try:
                pct = float(event.raw_text.strip())
            except Exception:
                await event.respond("Send a valid percent (1-100).")
                return
            if pct <= 0 or pct > 100:
                await event.respond("Percent must be 1-100.")
                return
            pending.pop(user_id, None)
            await event.respond(
                f"Confirm sell:\nMINT={mint}\nPCT={pct}",
                buttons=_confirm_buttons(f"sell:{mint}:{pct}"),
            )
            return

        if state.get("mode") == "setting_value":
            field = state.get("field")
            try:
                val = float(event.raw_text.strip())
            except Exception:
                await event.respond("Send a valid number.")
                return
            pending.pop(user_id, None)
            updates = {field: val}
            try:
                update_user_settings(user_id, updates)
                s = get_user_settings(user_id)
                await event.respond("Settings updated.", buttons=_settings_menu(s))
            except Exception as e:
                s = get_user_settings(user_id)
                await event.respond(f"Update failed: {e}", buttons=_settings_menu(s))
            return

        if state.get("mode") == "channels_add":
            handle = event.raw_text.strip()
            if not handle:
                await event.respond("Send a channel handle like @example.")
                return
            if not handle.startswith("@"):
                handle = f"@{handle}"
            pending.pop(user_id, None)
            upsert_subscription(user_id, handle, "ACTIVE")
            await event.respond(f"Added subscription: {handle}", buttons=_channels_menu())
            return

        if state.get("mode") == "channels_remove":
            handle = event.raw_text.strip()
            if not handle:
                await event.respond("Send a channel handle like @example.")
                return
            if not handle.startswith("@"):
                handle = f"@{handle}"
            pending.pop(user_id, None)
            upsert_subscription(user_id, handle, "DELETED")
            await event.respond(f"Removed subscription: {handle}", buttons=_channels_menu())
            return

    async def _send_mint_card(event, user_id: str, mint: str):
        last_mint[user_id] = mint
        s = get_user_settings(user_id)
        sol_in = float(s.get("buy_amount_sol") or 0.0)
        info_lines = [f"MINT: {mint}"]

        try:
            q = quote_buy_pumpfun(mint, sol_in=sol_in, fee_bps=0)
            info_lines.append(f"EST TOKENS (for {sol_in} SOL): {q.est_tokens_out_ui:.6f}" if q.est_tokens_out_ui else "")
            if q.est_price_sol_per_token:
                info_lines.append(f"PRICE: {q.est_price_sol_per_token:.12f} SOL")
        except Exception:
            pass

        try:
            mi = fetch_mint_info(mint)
            if mi and mi.decimals is not None and mi.supply is not None:
                supply_ui = mi.supply / (10 ** int(mi.decimals))
                if "PRICE:" in "\n".join(info_lines):
                    price_line = next((l for l in info_lines if l.startswith("PRICE:")), None)
                    if price_line:
                        price = float(price_line.split(" ")[1])
                        mcap = price * supply_ui
                        info_lines.append(f"MCAP (est): {mcap:,.2f} SOL")
        except Exception:
            pass

        rows = list_positions(user_id)
        has_pos = any(r["mint"] == mint and float(r["token_balance"]) > 0 for r in rows)
        buttons = _buy_amount_presets(mint)
        if has_pos:
            buttons = [
                [Button.inline("Sell Presets", f"sellpick:{mint}".encode("utf-8"))],
                *buttons,
            ]
        buttons.append([Button.inline("Refresh", b"mint:refresh"), Button.inline("Main Menu", b"menu:main")])
        await event.respond("\n".join([l for l in info_lines if l]), buttons=buttons)

    @client.on(events.CallbackQuery)
    async def _callbacks(event):
        user_id = str(event.sender_id)
        data = event.data.decode("utf-8")

        if data == "menu:main":
            await event.edit("Main menu:", buttons=_main_menu())
            return
        if data == "menu:wallet":
            pub = wallet_get_pubkey(user_id)
            text = f"wallet={pub}" if pub else "No wallet found."
            await event.edit(text, buttons=_wallet_menu())
            return
        if data == "wallet:generate":
            await _safe_edit(event, "Generating wallet...", buttons=_wallet_menu())
            try:
                out = wallet_create(user_id)
                await event.respond(
                    "Wallet created.\n"
                    f"pubkey={out['pubkey']}\n\n"
                    "Backup options:\n"
                    f"1) Phantom secret key (base58):\n{out['phantom_secret_base58']}\n\n"
                    f"2) Phantom secret key (JSON):\n{out['phantom_secret_json']}\n\n"
                    f"3) Seed (base58):\n{out['seed_base58']}",
                    buttons=_wallet_menu(),
                )
            except Exception as e:
                await event.respond(f"Generate failed: {e}", buttons=_wallet_menu())
            return
        if data == "wallet:import":
            pending[user_id] = {"mode": "import_wallet"}
            await _safe_edit(event, "Import wallet selected.", buttons=_wallet_menu())
            msg = await event.respond("Reply with the secret key or seed to import:", buttons=Button.force_reply())
            pending[user_id]["prompt_id"] = msg.id
            return
        if data == "menu:positions":
            rows = list_positions(user_id)
            if not rows:
                await _safe_edit(event, "No positions.", buttons=_main_menu())
                return
            lines = []
            for r in rows:
                lines.append(
                    f"{r['mint']} | tokens={r['token_balance']} | avg_entry={r['avg_entry_sol']} | "
                    f"pnl={r['realized_pnl_sol']} | open={r['open']}"
                )
            await _safe_edit(event, "\n".join(lines), buttons=_main_menu())
            return
        if data == "menu:settings":
            s = get_user_settings(user_id)
            await _safe_edit(
                event,
                "Settings (tap a row, then reply with a value when prompted):",
                buttons=_settings_menu(s),
            )
            return
        if data == "menu:channels":
            await _safe_edit(event, "Channel subscriptions:", buttons=_channels_menu())
            return
        if data == "menu:help":
            await _safe_edit(
                event,
                "Commands:\n"
                "/buy <mint> [sol]\n"
                "/sell <mint> <pct>\n"
                "/positions\n"
                "/status\n"
                "/wallet\n"
                "/import <secret>",
                buttons=_main_menu(),
            )
            return
        if data == "mint:refresh":
            mint = last_mint.get(user_id)
            if not mint:
                await _safe_edit(event, "No recent mint. Paste a CA.", buttons=_main_menu())
                return
            await _send_mint_card(event, user_id, mint)
            return
        if data == "menu:buy":
            pending[user_id] = {"mode": "buy_mint"}
            await _safe_edit(event, "Buy selected.", buttons=_main_menu())
            msg = await event.respond("Reply with the mint address to buy:", buttons=Button.force_reply())
            pending[user_id]["prompt_id"] = msg.id
            return
        if data == "menu:sell":
            rows = list_positions(user_id)
            open_rows = [r for r in rows if float(r["token_balance"]) > 0]
            if not open_rows:
                await _safe_edit(event, "No positions to sell.", buttons=_main_menu())
                return
            if len(open_rows) == 1:
                mint = open_rows[0]["mint"]
                await _safe_edit(event, f"Sell presets for {mint}:", buttons=_sell_presets(mint))
                return
            buttons = [[Button.inline(r["mint"][:8], f"sellpick:{r['mint']}".encode("utf-8"))] for r in open_rows]
            buttons.append([Button.inline("Back", b"menu:main")])
            await _safe_edit(event, "Select a mint:", buttons=buttons)
            return

        if data.startswith("buyamt:"):
            _tag, mint, amount = data.split(":")
            if amount == "custom":
                pending[user_id] = {"mode": "buy_amount_custom", "mint": mint}
                await _safe_edit(event, "Custom amount selected.", buttons=_buy_amount_presets(mint))
                msg = await event.respond("Reply with custom SOL amount:", buttons=Button.force_reply())
                pending[user_id]["prompt_id"] = msg.id
                return
            sol = float(amount)
            await _safe_edit(
                f"Confirm buy:\nMINT={mint}\nSOL={sol}",
                buttons=_confirm_buttons(f"buy:{mint}:{sol}"),
            )
            return

        if data.startswith("sellpick:"):
            mint = data.split(":", 1)[1]
            await _safe_edit(event, f"Sell presets for {mint}:", buttons=_sell_presets(mint))
            return
        if data.startswith("sell:"):
            _tag, mint, pct_s = data.split(":")
            pct = float(pct_s)
            pos = list_positions(user_id)
            row = next((r for r in pos if r["mint"] == mint), None)
            if not row or float(row["token_balance"]) <= 0:
                await _safe_edit(event, "No position balance found.", buttons=_main_menu())
                return
            tokens = float(row["token_balance"]) * (pct / 100.0)
            await _safe_edit(
                f"Confirm sell:\nMINT={mint}\nPCT={pct}",
                buttons=_confirm_buttons(f"sell:{mint}:{pct}"),
            )
            pending[user_id] = {"mode": "sell_confirm", "mint": mint, "pct": pct}
            return

        if data.startswith("confirm:"):
            _tag, action, mint, amt = data.split(":")
            if action == "buy":
                sol = float(amt)
                await _safe_edit(event, "Submitting buy...", buttons=_buy_amount_presets(mint))
                try:
                    sig = await asyncio.to_thread(auto_buy_for_user, user_id, mint, sol)
                    await event.respond(f"Buy submitted: {sig}")
                except Exception as e:
                    await event.respond(
                        f"Buy failed.\nError: {e}",
                        buttons=_retry_buy_buttons(mint, sol),
                    )
                return
            if action == "sell":
                pct = float(amt)
                pos = list_positions(user_id)
                row = next((r for r in pos if r["mint"] == mint), None)
                if not row or float(row["token_balance"]) <= 0:
                    await _safe_edit(event, "No position balance found.", buttons=_main_menu())
                    return
                tokens = float(row["token_balance"]) * (pct / 100.0)
                await _safe_edit(event, "Submitting sell...", buttons=_main_menu())
                sig = await asyncio.to_thread(auto_sell_for_position, user_id, mint, tokens)
                await event.respond(f"Sell submitted: {sig}")
                return

        if data.startswith("retry_buy:"):
            _tag, mint, sol_s = data.split(":")
            sol = float(sol_s)
            await _safe_edit(event, "Retrying buy...", buttons=_buy_amount_presets(mint))
            try:
                sig = await asyncio.to_thread(auto_buy_for_user, user_id, mint, sol)
                await event.respond(f"Buy submitted: {sig}")
            except Exception as e:
                await event.respond(
                    f"Buy failed.\nError: {e}",
                    buttons=_retry_buy_buttons(mint, sol),
                )
            return
        if data == "set:buy_amount":
            pending[user_id] = {"mode": "setting_value", "field": "buy_amount_sol"}
            s = get_user_settings(user_id)
            await _safe_edit(event, "Buy amount selected.", buttons=_settings_menu(s))
            msg = await event.respond("Reply with new buy amount (SOL):", buttons=Button.force_reply())
            pending[user_id]["prompt_id"] = msg.id
            return
        if data == "set:buy_slippage":
            pending[user_id] = {"mode": "setting_value", "field": "buy_slippage_pct"}
            s = get_user_settings(user_id)
            await _safe_edit(event, "Buy slippage selected.", buttons=_settings_menu(s))
            msg = await event.respond("Reply with new buy slippage (%):", buttons=Button.force_reply())
            pending[user_id]["prompt_id"] = msg.id
            return
        if data == "set:sell_slippage":
            pending[user_id] = {"mode": "setting_value", "field": "sell_slippage_pct"}
            s = get_user_settings(user_id)
            await _safe_edit(event, "Sell slippage selected.", buttons=_settings_menu(s))
            msg = await event.respond("Reply with new sell slippage (%):", buttons=Button.force_reply())
            pending[user_id]["prompt_id"] = msg.id
            return
        if data == "set:gas_fee":
            pending[user_id] = {"mode": "setting_value", "field": "gas_fee_sol"}
            s = get_user_settings(user_id)
            await _safe_edit(event, "Gas fee selected.", buttons=_settings_menu(s))
            msg = await event.respond("Reply with new gas fee (SOL):", buttons=Button.force_reply())
            pending[user_id]["prompt_id"] = msg.id
            return
        if data == "set:tp_sl_toggle":
            s = get_user_settings(user_id)
            new_val = 0 if int(s.get("tp_sl_enabled", 1)) else 1
            update_user_settings(user_id, {"tp_sl_enabled": new_val})
            s = get_user_settings(user_id)
            await _safe_edit(event, f"TP/SL enabled={new_val}", buttons=_settings_menu(s))
            return
        if data == "set:auto_buy_toggle":
            s = get_user_settings(user_id)
            new_val = 0 if int(s.get("auto_buy_enabled", 1)) else 1
            update_user_settings(user_id, {"auto_buy_enabled": new_val})
            s = get_user_settings(user_id)
            await _safe_edit(event, f"Auto buy enabled={new_val}", buttons=_settings_menu(s))
            return
        if data == "set:dup_toggle":
            s = get_user_settings(user_id)
            new_val = 0 if int(s.get("duplicate_mint_block", 1)) else 1
            update_user_settings(user_id, {"duplicate_mint_block": new_val})
            s = get_user_settings(user_id)
            await _safe_edit(event, f"Duplicate block={new_val}", buttons=_settings_menu(s))
            return
        if data == "set:take_profit":
            pending[user_id] = {"mode": "setting_value", "field": "take_profit_pct"}
            s = get_user_settings(user_id)
            await _safe_edit(event, "Take profit selected.", buttons=_settings_menu(s))
            msg = await event.respond("Reply with take profit (%):", buttons=Button.force_reply())
            pending[user_id]["prompt_id"] = msg.id
            return
        if data == "set:stop_loss":
            pending[user_id] = {"mode": "setting_value", "field": "stop_loss_pct"}
            s = get_user_settings(user_id)
            await _safe_edit(event, "Stop loss selected.", buttons=_settings_menu(s))
            msg = await event.respond("Reply with stop loss (%):", buttons=Button.force_reply())
            pending[user_id]["prompt_id"] = msg.id
            return

        if data == "channels:list":
            rows = list_subscriptions(user_id)
            if not rows:
                await _safe_edit(event, "No subscriptions found.", buttons=_channels_menu())
                return
            lines = [f"{r['handle']} | {r['status']} | {r['created_at']}" for r in rows]
            await _safe_edit(event, "\n".join(lines), buttons=_channels_menu())
            return
        if data == "channels:add":
            pending[user_id] = {"mode": "channels_add"}
            await _safe_edit(event, "Add channel selected.", buttons=_channels_menu())
            msg = await event.respond("Reply with a channel handle to add (e.g., @example):", buttons=Button.force_reply())
            pending[user_id]["prompt_id"] = msg.id
            return
        if data == "channels:remove":
            pending[user_id] = {"mode": "channels_remove"}
            await _safe_edit(event, "Remove channel selected.", buttons=_channels_menu())
            msg = await event.respond("Reply with a channel handle to remove:", buttons=Button.force_reply())
            pending[user_id]["prompt_id"] = msg.id
            return

    await client.run_until_disconnected()
