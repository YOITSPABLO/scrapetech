import base64
import time
from typing import Optional

from .db import (
    apply_trade,
    enqueue_pending_trade,
    update_pending_trade_status,
    get_telegram_user_id,
    get_position,
    list_positions,
    get_user_settings,
    get_pending_trade,
    reconcile_position_balance,
)
from .pump_tx import send_buy_tx, load_keypair_for_user
from .pump_sell import build_sell_ix_and_plan, send_sell_tx
from .solana_rpc import (
    get_http_client,
    rpc_get_transaction,
    rpc_get_signature_status,
    extract_tx_deltas,
    try_get_mint_decimals,
    rpc_get_multiple_accounts,
    rpc_get_token_balance_for_owner_mint,
    rpc_get_token_balance_for_owner_mint_any,
)
from .pump_quotes import get_bonding_curve_pda, decode_bonding_curve_state


class TxFailed(Exception):
    def __init__(self, sig: str, err: str):
        super().__init__(f"Transaction failed: {err}")
        self.sig = sig
        self.err = err


class TxPending(Exception):
    def __init__(self, sig: str, note: str):
        super().__init__(note)
        self.sig = sig
        self.note = note


def _wait_for_receipt(
    signature: str, owner_pubkey: str, mint: str, retries: int = 6, delay: int = 2
):
    http = get_http_client()
    confirmed_seen = False
    for _ in range(retries):
        try:
            tx = rpc_get_transaction(http, signature)
            if tx and tx.get("meta") and tx["meta"].get("err"):
                return None, None, str(tx["meta"]["err"])
            if not tx:
                status = rpc_get_signature_status(http, signature)
                if status and status.get("err"):
                    return None, None, str(status["err"])
                if status and status.get("confirmationStatus") in ("processed", "confirmed", "finalized"):
                    confirmed_seen = True
            deltas = extract_tx_deltas(tx, owner_pubkey=owner_pubkey, mint=mint)
            sol_delta = deltas.get("sol_delta_lamports")
            token_delta_ui = deltas.get("token_delta_ui")
            if sol_delta is not None and token_delta_ui is not None:
                return sol_delta, token_delta_ui, None
            if tx and tx.get("meta") and not tx["meta"].get("err"):
                return sol_delta, None, "MISSING_DELTAS"
        except Exception:
            pass
        time.sleep(delay)
    if confirmed_seen:
        return None, None, "RECEIPT_PENDING"
    return None, None, "Transaction not found on-chain"


def submit_buy_for_user(
    telegram_user_id: str,
    mint: str,
    sol_in: Optional[float] = None,
    slippage_pct: Optional[float] = None,
    auto_buy_enabled: Optional[bool] = None,
) -> tuple[str, str, str]:
    settings = get_user_settings(telegram_user_id)
    enabled = int(settings.get("auto_buy_enabled", 1)) if auto_buy_enabled is None else int(auto_buy_enabled)
    if not enabled:
        raise ValueError("Auto-buy is disabled for this user")
    sol_in = float(sol_in) if sol_in is not None else float(settings["buy_amount_sol"])
    slippage = float(slippage_pct) if slippage_pct is not None else float(settings["buy_slippage_pct"])

    kp = load_keypair_for_user(telegram_user_id)
    out = send_buy_tx(user_keypair=kp, mint_str=mint, sol_in=sol_in, slippage_pct=slippage)
    sig = out["sig"]
    plan = out["plan"]

    req_tokens_ui = None
    decimals = try_get_mint_decimals(plan.mint)
    if decimals is not None:
        req_tokens_ui = plan.tokens_out_raw / (10 ** int(decimals))

    enqueue_pending_trade(
        telegram_user_id,
        plan.mint,
        "BUY",
        sig,
        requested_token_amount=req_tokens_ui,
        requested_sol_amount=sol_in,
    )
    return sig, plan.user_pubkey, plan.mint


def confirm_trade(
    telegram_user_id: str,
    signature: str,
    mint: str,
    owner_pubkey: str,
    side: str,
    retries: int = 15,
    delay: int = 2,
):
    side = side.strip().upper()
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")

    sol_delta, token_delta_ui, err = _wait_for_receipt(signature, owner_pubkey, mint, retries=retries, delay=delay)
    if err:
        if err == "MISSING_DELTAS":
            pending = get_pending_trade(signature)
            sol_amount = abs(sol_delta) / 1_000_000_000 if sol_delta is not None else 0.0
            if side == "BUY":
                if pending and pending.get("requested_sol_amount"):
                    sol_amount = float(pending["requested_sol_amount"])
            try:
                http = get_http_client()
                onchain_bal = rpc_get_token_balance_for_owner_mint_any(http, owner_pubkey, mint)
            except Exception:
                onchain_bal = None

            token_amount_ui = None
            if onchain_bal is not None:
                prev = get_position(telegram_user_id, mint)
                prev_bal = float(prev["token_balance"]) if prev else 0.0
                delta = float(onchain_bal) - prev_bal if side == "BUY" else prev_bal - float(onchain_bal)
                if delta > 0:
                    token_amount_ui = delta

            if token_amount_ui is None and pending and pending.get("requested_token_amount"):
                token_amount_ui = float(pending["requested_token_amount"])

            if sol_amount > 0 and token_amount_ui and token_amount_ui > 0:
                try:
                    apply_trade(telegram_user_id, mint, side, token_amount_ui, sol_amount, tx_sig=signature)
                    update_pending_trade_status(
                        signature,
                        "SUCCESS",
                        actual_token_amount=token_amount_ui,
                        actual_sol_amount=sol_amount,
                    )
                    return {
                        "status": "SUCCESS",
                        "signature": signature,
                        "token_amount": token_amount_ui,
                        "sol_amount": sol_amount,
                    }
                except Exception:
                    pass
            return {"status": "PENDING", "signature": signature, "error": "Missing token deltas"}
        if err == "RECEIPT_PENDING":
            return {"status": "PENDING", "signature": signature, "error": None}
        update_pending_trade_status(signature, "FAILED", error=err)
        return {"status": "FAILED", "signature": signature, "error": err}

    if sol_delta is not None and token_delta_ui is not None:
        sol_amount = abs(sol_delta) / 1_000_000_000
        token_amount_ui = abs(token_delta_ui)
        try:
            apply_trade(telegram_user_id, mint, side, token_amount_ui, sol_amount, tx_sig=signature)
            update_pending_trade_status(
                signature,
                "SUCCESS",
                actual_token_amount=token_amount_ui,
                actual_sol_amount=sol_amount,
            )
            http = get_http_client()
            onchain_bal = rpc_get_token_balance_for_owner_mint_any(http, owner_pubkey, mint)
            if onchain_bal is not None:
                reconcile_position_balance(telegram_user_id, mint, float(onchain_bal))
        except Exception:
            pass
        return {
            "status": "SUCCESS",
            "signature": signature,
            "token_amount": token_amount_ui,
            "sol_amount": sol_amount,
        }

    return {"status": "PENDING", "signature": signature, "error": None}


def auto_buy_for_user(telegram_user_id: str, mint: str, sol_in: Optional[float] = None) -> Optional[str]:
    sig, owner_pubkey, mint = submit_buy_for_user(telegram_user_id, mint, sol_in=sol_in)

    res = confirm_trade(telegram_user_id, sig, mint, owner_pubkey, "BUY")
    if res.get("status") == "FAILED":
        raise TxFailed(sig, res.get("error") or "Unknown error")
    if res.get("status") == "PENDING":
        raise TxPending(sig, "Transaction submitted; awaiting receipt")

    return sig


def _current_price_sol_per_token(mint: str) -> Optional[float]:
    curve_pda = get_bonding_curve_pda(mint)
    http = get_http_client()
    vals = rpc_get_multiple_accounts(http, [str(curve_pda)])
    acct = vals[0] if vals else None
    if not acct or "data" not in acct:
        return None
    data = acct.get("data")
    if isinstance(data, list) and data:
        data_b64 = data[0]
    elif isinstance(data, str):
        data_b64 = data
    else:
        return None

    data = base64.b64decode(data_b64)
    st = decode_bonding_curve_state(data)
    if st.virtual_token_reserves <= 0 or st.virtual_sol_reserves <= 0:
        return None
    return (st.virtual_sol_reserves / 1_000_000_000) / st.virtual_token_reserves


def submit_sell_for_user(
    telegram_user_id: str, mint: str, tokens_ui: float
) -> tuple[str, str, str]:
    decimals = try_get_mint_decimals(mint)
    if decimals is None:
        raise ValueError("Could not determine mint decimals for sell sizing")
    tokens_raw = int(tokens_ui * (10 ** int(decimals)))
    if tokens_raw <= 0:
        raise ValueError("tokens_to_sell_raw computed as 0")

    kp = load_keypair_for_user(telegram_user_id)
    plan, sell_ix = build_sell_ix_and_plan(
        user_keypair=kp,
        mint_str=mint,
        tokens_to_sell_raw=tokens_raw,
        min_sol_output_lamports=1,
    )
    sig = send_sell_tx(user_keypair=kp, sell_ix=sell_ix)

    enqueue_pending_trade(
        telegram_user_id,
        plan.mint,
        "SELL",
        sig,
        requested_token_amount=tokens_ui,
        requested_sol_amount=0.0,
    )
    return sig, plan.user_pubkey, plan.mint

def auto_sell_for_position(telegram_user_id: str, mint: str, tokens_ui: float) -> Optional[str]:
    sig, owner_pubkey, mint = submit_sell_for_user(telegram_user_id, mint, tokens_ui)

    res = confirm_trade(telegram_user_id, sig, mint, owner_pubkey, "SELL")
    if res.get("status") == "FAILED":
        raise TxFailed(sig, res.get("error") or "Unknown error")
    if res.get("status") == "PENDING":
        raise TxPending(sig, "Transaction submitted; awaiting receipt")

    return sig


def monitor_positions_loop(interval: int = 10):
    while True:
        rows = list_positions_for_monitor()
        for row in rows:
            _evaluate_position(row)
        time.sleep(max(1, int(interval)))


def list_positions_for_monitor():
    rows = []
    # get all positions for all users
    # list_positions expects telegram_user_id; query pending trades to get users
    # fetch users via sqlite directly for simplicity
    from .db import connect
    with connect() as conn:
        raw = conn.execute("SELECT DISTINCT user_id FROM positions WHERE open=1").fetchall()
    for r in raw:
        telegram_user_id = get_telegram_user_id(r["user_id"])
        if not telegram_user_id:
            continue
        rows.extend(list_positions(telegram_user_id))
    return rows


def _evaluate_position(pos):
    if not pos.get("open"):
        return

    telegram_user_id = get_telegram_user_id(pos["user_id"])
    if not telegram_user_id:
        return

    settings = get_user_settings(telegram_user_id)
    if not int(settings.get("tp_sl_enabled", 1)):
        return

    entry = float(pos.get("avg_entry_sol") or 0)
    if entry <= 0:
        return

    price = _current_price_sol_per_token(pos["mint"])
    if price is None:
        return

    tp = float(settings.get("take_profit_pct", 0))
    sl = float(settings.get("stop_loss_pct", 0))

    if tp > 0 and price >= entry * (1 + tp / 100.0):
        auto_sell_for_position(telegram_user_id, pos["mint"], float(pos["token_balance"]))
        return

    if sl > 0 and price <= entry * (1 - sl / 100.0):
        auto_sell_for_position(telegram_user_id, pos["mint"], float(pos["token_balance"]))
        return
