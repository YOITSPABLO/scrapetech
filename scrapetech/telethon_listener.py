import asyncio
import logging
import os
import time
from telethon import TelegramClient, events, utils
from telethon.tl.functions.channels import JoinChannelRequest
from .config import Settings
from .detector import detect_mints
from .db import (
    get_or_create_channel,
    insert_message,
    insert_signal,
    active_subscribers_for_channel,
    list_active_channels,
    get_effective_settings,
    update_listener_heartbeat,
    reconcile_position_balance,
    get_position,
    apply_trade,
    update_pending_trade_status,
    get_pending_trade,
)
from .auto_trader import submit_buy_for_user, confirm_trade
from .solana_rpc import get_http_client, rpc_get_token_balance_for_owner_mint_any
from .wallets import wallet_get_pubkey

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("scrapetech.listener")

def _bot_token() -> str | None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    return token or None

def _tx_link(sig: str) -> str:
    return f"https://solscan.io/tx/{sig}"

def _notify_bot(chat_id: str, text: str):
    token = _bot_token()
    if not token:
        log.warning("Bot token missing; cannot notify chat_id=%s", chat_id)
        return
    try:
        chat_id_int = int(chat_id)
    except Exception:
        log.warning("Invalid chat_id for bot notify: %s", chat_id)
        return
    import httpx
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id_int, "text": text}
    try:
        r = httpx.post(url, json=payload, timeout=10.0)
        if r.status_code != 200:
            log.warning("Bot notify failed chat_id=%s status=%s body=%s", chat_id, r.status_code, r.text)
        else:
            log.info("Bot notify ok chat_id=%s", chat_id)
    except Exception:
        log.exception("Bot notify failed chat_id=%s", chat_id)

async def run_listen(channel: str) -> None:
    settings = Settings.from_env()

    client = TelegramClient(
        settings.telethon_session,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

    await client.start()

    try:
        entity = await client.get_input_entity(channel)
    except Exception:
        log.error("Could not resolve channel '%s'. Check the @username and that your account can access it.", channel)
        raise

    channel_id = get_or_create_channel(channel)

    @client.on(events.NewMessage(chats=entity))
    async def handler(event):
        text = (event.raw_text or "").strip()
        if not text:
            return

        clean = text.replace("\n", " ")
        log.info("MSG %s | %s", event.id, clean[:120])

        message_id = insert_message(channel_id=channel_id, telegram_message_id=int(event.id), text=text)

        for dm in detect_mints(text):
            log.info("DETECTED mint=%s confidence=%s", dm.mint, dm.confidence)
            insert_signal(channel_id=channel_id, message_id=message_id, mint=dm.mint, confidence=int(dm.confidence))

            users = active_subscribers_for_channel(channel)
            if users:
                log.info("ROUTE mint=%s -> users=%s", dm.mint, users)
            else:
                log.info("ROUTE mint=%s -> users=[] (no active subscribers)", dm.mint)

            def _auto_buy_sync(user_id: str, mint: str):
                try:
                    s = get_effective_settings(user_id, handle)
                    if not int(s.get("auto_buy_enabled", 1)):
                        return
                    if not wallet_get_pubkey(user_id):
                        log.info("AUTO_BUY skipped (no wallet): user=%s mint=%s", user_id, mint)
                        return
                    sol_in = float(s.get("buy_amount_sol") or 0.0)
                    slippage = float(s.get("buy_slippage_pct") or 0.0)
                    sig, owner_pubkey, mint = submit_buy_for_user(
                        user_id, mint, sol_in=sol_in, slippage_pct=slippage, auto_buy_enabled=True
                    )
                    log.info(
                        "AUTO_BUY submit: user=%s mint=%s sig=%s sol_in=%s",
                        user_id,
                        mint,
                        sig,
                        sol_in,
                    )
                    tp_on = int(s.get("tp_sl_enabled", 1))
                    tp = s.get("take_profit_pct")
                    sl = s.get("stop_loss_pct")
                    tp_line = f"TP/SL: {tp}%/{sl}%" if tp_on else "TP/SL: off"
                    _notify_bot(
                        user_id,
                        f"Auto-buy submitted.\nMint: {mint}\nAmount: {sol_in} SOL\n{tp_line}\nTx: {_tx_link(sig)}",
                    )
                    res = {"status": "PENDING"}
                    for _ in range(20):
                        res = confirm_trade(user_id, sig, mint, owner_pubkey, "BUY", retries=1, delay=1)
                        log.info(
                            "AUTO_BUY confirm: user=%s mint=%s sig=%s status=%s",
                            user_id,
                            mint,
                            sig,
                            res.get("status"),
                        )
                        if res.get("status") != "PENDING":
                            break
                        time.sleep(2)
                    if res.get("status") == "PENDING":
                        try:
                            http = get_http_client()
                            bal = rpc_get_token_balance_for_owner_mint_any(http, owner_pubkey, mint)
                            if bal is not None:
                                log.info(
                                    "AUTO_BUY reconcile: user=%s mint=%s onchain_bal=%s",
                                    user_id,
                                    mint,
                                    bal,
                                )
                                prev = get_position(user_id, mint)
                                prev_bal = float(prev["token_balance"]) if prev else 0.0
                                delta = float(bal) - prev_bal
                                pending = get_pending_trade(sig)
                                sol_amt = None
                                if pending and pending.get("requested_sol_amount"):
                                    sol_amt = float(pending["requested_sol_amount"])
                                log.info(
                                    "AUTO_BUY delta: user=%s mint=%s prev=%s delta=%s sol=%s",
                                    user_id,
                                    mint,
                                    prev_bal,
                                    delta,
                                    sol_amt,
                                )
                                if delta > 0 and sol_amt is not None and sol_amt > 0:
                                    apply_trade(user_id, mint, "BUY", delta, sol_amt, tx_sig=sig)
                                    update_pending_trade_status(
                                        sig,
                                        "SUCCESS",
                                        actual_token_amount=delta,
                                        actual_sol_amount=sol_amt,
                                    )
                                    log.info(
                                        "AUTO_BUY apply_trade: user=%s mint=%s delta=%s sol=%s",
                                        user_id,
                                        mint,
                                        delta,
                                        sol_amt,
                                    )
                                else:
                                    reconcile_position_balance(user_id, mint, float(bal))
                        except Exception:
                            pass
                    if int(s.get("confirm_tx_enabled", 0)):
                        status = res.get("status")
                        if status == "SUCCESS":
                            _notify_bot(user_id, f"Auto-buy confirmed.\nTx: {_tx_link(sig)}")
                        elif status == "FAILED":
                            _notify_bot(user_id, f"Auto-buy failed.\nError: {res.get('error')}\nTx: {_tx_link(sig)}")
                    log.info("AUTO_BUY queued: user=%s mint=%s", user_id, mint)
                except Exception as e:
                    msg = str(e)
                    if "BondingCurveComplete" in msg or "custom program error: 0x1775" in msg:
                        _notify_bot(
                            user_id,
                            f"Auto-buy failed.\nReason: Bonding curve complete (migrated to Raydium).\nMint: {mint}",
                        )
                    else:
                        _notify_bot(user_id, f"Auto-buy failed.\nError: {e}\nMint: {mint}")
                    log.error("AUTO_BUY failed: user=%s mint=%s err=%s", user_id, mint, e)

            for u in users:
                asyncio.create_task(asyncio.to_thread(_auto_buy_sync, u, dm.mint))

    log.info("Listening on %s", channel)
    await client.run_until_disconnected()


async def run_listen_all(poll_seconds: int = 10) -> None:
    settings = Settings.from_env()

    client = TelegramClient(
        settings.telethon_session,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

    await client.start()

    watched = {}
    active_handles = set()

    async def _refresh_channels():
        handles = set(list_active_channels())
        log.info("Refresh channels: %s", sorted(handles))
        # add new subscriptions
        for handle in sorted(handles - active_handles):
            try:
                await client(JoinChannelRequest(handle))
            except Exception as e:
                log.warning("Join failed for %s: %s", handle, e)
            try:
                entity = await client.get_entity(handle)
                peer_id = int(utils.get_peer_id(entity))
                watched[peer_id] = handle
                active_handles.add(handle)
                get_or_create_channel(handle)
                log.info("Listening on %s (peer_id=%s)", handle, peer_id)
            except Exception as e:
                log.error("Could not resolve channel '%s': %s", handle, e)

        # remove inactive subscriptions
        removed = active_handles - handles
        if removed:
            for handle in list(removed):
                active_handles.discard(handle)
                for cid, h in list(watched.items()):
                    if h == handle:
                        watched.pop(cid, None)
                log.info("Stopped listening on %s", handle)

    @client.on(events.NewMessage)
    async def handler(event):
        if not event.chat_id:
            return
        handle = watched.get(int(event.chat_id))
        if not handle:
            return

        text = (event.raw_text or "").strip()
        if not text:
            return

        clean = text.replace("\n", " ")
        log.info("MSG %s | %s", event.id, clean[:120])

        channel_id = get_or_create_channel(handle)
        message_id = insert_message(channel_id=channel_id, telegram_message_id=int(event.id), text=text)

        for dm in detect_mints(text):
            log.info("DETECTED mint=%s confidence=%s", dm.mint, dm.confidence)
            insert_signal(channel_id=channel_id, message_id=message_id, mint=dm.mint, confidence=int(dm.confidence))

            users = active_subscribers_for_channel(handle)
            if users:
                log.info("ROUTE mint=%s -> users=%s", dm.mint, users)
            else:
                log.info("ROUTE mint=%s -> users=[] (no active subscribers)", dm.mint)

            def _auto_buy_sync(user_id: str, mint: str):
                try:
                    s = get_effective_settings(user_id, handle)
                    if not int(s.get("auto_buy_enabled", 1)):
                        return
                    if not wallet_get_pubkey(user_id):
                        log.info("AUTO_BUY skipped (no wallet): user=%s mint=%s", user_id, mint)
                        return
                    sol_in = float(s.get("buy_amount_sol") or 0.0)
                    slippage = float(s.get("buy_slippage_pct") or 0.0)
                    sig, owner_pubkey, mint = submit_buy_for_user(
                        user_id, mint, sol_in=sol_in, slippage_pct=slippage, auto_buy_enabled=True
                    )
                    log.info(
                        "AUTO_BUY submit: user=%s mint=%s sig=%s sol_in=%s",
                        user_id,
                        mint,
                        sig,
                        sol_in,
                    )
                    tp_on = int(s.get("tp_sl_enabled", 1))
                    tp = s.get("take_profit_pct")
                    sl = s.get("stop_loss_pct")
                    tp_line = f"TP/SL: {tp}%/{sl}%" if tp_on else "TP/SL: off"
                    _notify_bot(
                        user_id,
                        f"Auto-buy submitted.\nMint: {mint}\nAmount: {sol_in} SOL\n{tp_line}\nTx: {_tx_link(sig)}",
                    )
                    res = {"status": "PENDING"}
                    for _ in range(20):
                        res = confirm_trade(user_id, sig, mint, owner_pubkey, "BUY", retries=1, delay=1)
                        log.info(
                            "AUTO_BUY confirm: user=%s mint=%s sig=%s status=%s",
                            user_id,
                            mint,
                            sig,
                            res.get("status"),
                        )
                        if res.get("status") != "PENDING":
                            break
                        time.sleep(2)
                    if res.get("status") == "PENDING":
                        try:
                            http = get_http_client()
                            bal = rpc_get_token_balance_for_owner_mint_any(http, owner_pubkey, mint)
                            if bal is not None:
                                log.info(
                                    "AUTO_BUY reconcile: user=%s mint=%s onchain_bal=%s",
                                    user_id,
                                    mint,
                                    bal,
                                )
                                prev = get_position(user_id, mint)
                                prev_bal = float(prev["token_balance"]) if prev else 0.0
                                delta = float(bal) - prev_bal
                                pending = get_pending_trade(sig)
                                sol_amt = None
                                if pending and pending.get("requested_sol_amount"):
                                    sol_amt = float(pending["requested_sol_amount"])
                                log.info(
                                    "AUTO_BUY delta: user=%s mint=%s prev=%s delta=%s sol=%s",
                                    user_id,
                                    mint,
                                    prev_bal,
                                    delta,
                                    sol_amt,
                                )
                                if delta > 0 and sol_amt is not None and sol_amt > 0:
                                    apply_trade(user_id, mint, "BUY", delta, sol_amt, tx_sig=sig)
                                    update_pending_trade_status(
                                        sig,
                                        "SUCCESS",
                                        actual_token_amount=delta,
                                        actual_sol_amount=sol_amt,
                                    )
                                    log.info(
                                        "AUTO_BUY apply_trade: user=%s mint=%s delta=%s sol=%s",
                                        user_id,
                                        mint,
                                        delta,
                                        sol_amt,
                                    )
                                else:
                                    reconcile_position_balance(user_id, mint, float(bal))
                        except Exception:
                            pass
                    if int(s.get("confirm_tx_enabled", 0)):
                        status = res.get("status")
                        if status == "SUCCESS":
                            _notify_bot(user_id, f"Auto-buy confirmed.\nTx: {_tx_link(sig)}")
                        elif status == "FAILED":
                            _notify_bot(user_id, f"Auto-buy failed.\nError: {res.get('error')}\nTx: {_tx_link(sig)}")
                    log.info("AUTO_BUY queued: user=%s mint=%s", user_id, mint)
                except Exception as e:
                    msg = str(e)
                    if "BondingCurveComplete" in msg or "custom program error: 0x1775" in msg:
                        _notify_bot(
                            user_id,
                            f"Auto-buy failed.\nReason: Bonding curve complete (migrated to Raydium).\nMint: {mint}",
                        )
                    else:
                        _notify_bot(user_id, f"Auto-buy failed.\nError: {e}\nMint: {mint}")
                    log.error("AUTO_BUY failed: user=%s mint=%s err=%s", user_id, mint, e)

            for u in users:
                asyncio.create_task(asyncio.to_thread(_auto_buy_sync, u, dm.mint))

    async def _poll_loop():
        while True:
            await _refresh_channels()
            update_listener_heartbeat()
            log.info("Heartbeat updated")
            await asyncio.sleep(max(2, int(poll_seconds)))

    asyncio.create_task(_poll_loop())
    log.info("Listening on all active channels")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(run_listen_all())
