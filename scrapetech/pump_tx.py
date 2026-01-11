from __future__ import annotations

import os
import json
import base64
import struct
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Any

from solders.pubkey import Pubkey
from solders.hash import Hash
from solders.keypair import Keypair
from solders.instruction import Instruction, AccountMeta
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
from solders.system_program import ID as SYS_PROGRAM_ID

from .solana_rpc import get_http_client, rpc_get_latest_blockhash, rpc_get_multiple_accounts, _rpc_url
from .pump_quotes import quote_buy_pumpfun
from .wallets import wallet_get_keypair  # must exist in your wallets.py


# -----------------------
# Constants (from scalper)
# -----------------------
PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
GLOBAL = Pubkey.from_string("4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf")
FEE_RECIPIENT = Pubkey.from_string("CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM")
EVENT_AUTHORITY = Pubkey.from_string("Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1")
ASSOCIATED_TOKEN_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
FEE_CONFIG = Pubkey.from_string("8Wf5TiAheLUqBrKXeYg2JtAFFMWtKdG2BSFgqUcPVwTt")
FEE_PROGRAM = Pubkey.from_string("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ")
GLOBAL_VOLUME = Pubkey.from_string("Hq2wp8uJ9jCPsYgNHex8RtqdvMPfVGoYwjvF1ATiwn2Y")

BUY_DISCRIMINATOR = b"\x66\x06\x3d\x12\x01\xda\xeb\xea"

def _fetch_mint_owner_program(mint_str: str) -> str:
    """
    Returns the owner program id of the mint account (Tokenkeg... or Tokenz...).
    """
    http = get_http_client()
    # getMultipleAccounts returns list aligned with pubkeys
    vals = rpc_get_multiple_accounts(http, [mint_str])
    acc = vals[0]
    if not acc:
        raise ValueError(f"Mint account not found for {mint_str}")
    owner = acc.get("owner")
    if not owner:
        raise ValueError("RPC mint response missing owner")
    return owner



# -----------------------
# Helpers
# -----------------------
def _get_associated_token_address(owner: Pubkey, mint: Pubkey, token_program: Pubkey) -> Pubkey:
    pda, _ = Pubkey.find_program_address(
        [bytes(owner), bytes(token_program), bytes(mint)],
        ASSOCIATED_TOKEN_PROGRAM_ID,
    )
    return pda


def _get_bonding_curve_pda(mint: Pubkey) -> Pubkey:
    pda, _ = Pubkey.find_program_address([b"bonding-curve", bytes(mint)], PUMP_PROGRAM)
    return pda


def _get_user_volume_accumulator(owner: Pubkey) -> Pubkey:
    pda, _ = Pubkey.find_program_address([b"user_volume_accumulator", bytes(owner)], PUMP_PROGRAM)
    return pda


def _get_creator_vault_pda(creator: Pubkey) -> Pubkey:
    pda, _ = Pubkey.find_program_address([b"creator-vault", bytes(creator)], PUMP_PROGRAM)
    return pda


def ata_create_idempotent_ix(payer: Pubkey, owner: Pubkey, mint: Pubkey, ata: Pubkey, token_program: Pubkey) -> Instruction:
    """
    EXACTLY match your scalper's ATA create:
      program = Associated Token Program
      data = b'\\x01' (CreateIdempotent)
      accounts = [payer, ata, owner, mint, system_program, token_program]
    """
    return Instruction(
        ASSOCIATED_TOKEN_PROGRAM_ID,
        b"\x01",
        [
            AccountMeta(payer, True, True),
            AccountMeta(ata, False, True),
            AccountMeta(owner, False, False),
            AccountMeta(mint, False, False),
            AccountMeta(SYS_PROGRAM_ID, False, False),
            AccountMeta(token_program, False, False),
        ],
    )


@dataclass
class PumpBuyPlan:
    user_pubkey: str
    mint: str
    token_program: str
    bonding_curve: str
    curve_ata: str
    user_ata: str
    creator: str
    creator_vault: str
    user_volume_accumulator: str
    tokens_out_raw: int
    max_sol_cost_lamports: int
    slippage_pct: float


def build_buy_ix_and_plan(
    user_keypair: Keypair,
    mint_str: str,
    sol_in: float,
    slippage_pct: float,
) -> Tuple[PumpBuyPlan, Instruction]:
    """
    Builds the Pump.fun BUY instruction with the exact account order from your scalper.
    Uses quote_buy_pumpfun() for curve/creator/tokens_out estimate.
    """
    q = quote_buy_pumpfun(mint_str, sol_in=sol_in, fee_bps=0)

    mint = Pubkey.from_string(mint_str)
    token_program = Pubkey.from_string(_fetch_mint_owner_program(mint_str))
    curve = Pubkey.from_string(q.curve_pda)
    curve_ata = _get_associated_token_address(curve, mint, token_program)
    user = user_keypair.pubkey()
    user_ata = _get_associated_token_address(user, mint, token_program)

    creator = Pubkey.from_string(q.creator)
    creator_vault = _get_creator_vault_pda(creator)
    uva = _get_user_volume_accumulator(user)

    sol_lamports = int(sol_in * 1_000_000_000)
    max_sol = int(sol_lamports * (1.0 + (slippage_pct / 100.0)))

    amount_out = int(q.est_tokens_out_raw)

    data = BUY_DISCRIMINATOR + struct.pack("<Q", amount_out) + struct.pack("<Q", max_sol)

    ix = Instruction(
        PUMP_PROGRAM,
        data,
        [
            AccountMeta(GLOBAL, False, False),
            AccountMeta(FEE_RECIPIENT, False, True),
            AccountMeta(mint, False, False),
            AccountMeta(curve, False, True),
            AccountMeta(curve_ata, False, True),
            AccountMeta(user_ata, False, True),
            AccountMeta(user, True, True),
            AccountMeta(SYS_PROGRAM_ID, False, False),
            AccountMeta(token_program, False, False),
            AccountMeta(creator_vault, False, True),
            AccountMeta(EVENT_AUTHORITY, False, False),
            AccountMeta(PUMP_PROGRAM, False, False),
            AccountMeta(GLOBAL_VOLUME, False, False),
            AccountMeta(uva, False, True),
            AccountMeta(FEE_CONFIG, False, False),
            AccountMeta(FEE_PROGRAM, False, False),
        ],
    )

    plan = PumpBuyPlan(
        user_pubkey=str(user),
        mint=mint_str,
        token_program=str(token_program),
        bonding_curve=str(curve),
        curve_ata=str(curve_ata),
        user_ata=str(user_ata),
        creator=str(creator),
        creator_vault=str(creator_vault),
        user_volume_accumulator=str(uva),
        tokens_out_raw=amount_out,
        max_sol_cost_lamports=max_sol,
        slippage_pct=slippage_pct,
    )
    return plan, ix


def dump_ix_accounts(ix: Instruction) -> List[Tuple[int, bool, bool, str]]:
    out = []
    for i, m in enumerate(ix.accounts):
        out.append((i, bool(m.is_signer), bool(m.is_writable), str(m.pubkey)))
    return out


def _pubkeys_exist(pubkeys: List[Pubkey]) -> List[str]:
    """
    Returns pubkeys that do NOT exist on-chain (account is None).
    Note: unfunded wallet system accounts can show as missing.
    """
    http = get_http_client()
    resp = rpc_get_multiple_accounts(http, [str(p) for p in pubkeys])
    missing = []
    for pk, acc in zip(pubkeys, resp):
        if acc is None:
            missing.append(str(pk))
    return missing


def build_and_simulate_buy_tx(
    user_keypair: Keypair,
    mint_str: str,
    sol_in: float,
    slippage_pct: float,
) -> Dict[str, Any]:
    plan, buy_ix = build_buy_ix_and_plan(user_keypair, mint_str, sol_in, slippage_pct)

    user = Pubkey.from_string(plan.user_pubkey)
    mint = Pubkey.from_string(plan.mint)
    token_program = Pubkey.from_string(plan.token_program)
    user_ata = Pubkey.from_string(plan.user_ata)

    ata_ix = ata_create_idempotent_ix(user, user, mint, user_ata, token_program)

    # preflight existence check for debugging
    missing = _pubkeys_exist([user, user_ata, Pubkey.from_string(plan.user_volume_accumulator)])

    # build tx
    http = get_http_client()
    blockhash = rpc_get_latest_blockhash(http)

    msg = MessageV0.try_compile(user, [ata_ix, buy_ix], [], Hash.from_string(blockhash))
    tx = VersionedTransaction(msg, [user_keypair])

    # simulate
    sim = http.post(
        _rpc_url(),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "simulateTransaction",
            "params": [base64.b64encode(bytes(tx)).decode("utf-8"), {"encoding": "base64"}],
        },
        timeout=30.0,
    ).json()

    # normalize output
    sim_val = sim.get("result", {}).get("value", {})
    return {
        "plan": plan,
        "missing_tx_accounts": missing,
        "simulate": {
            "err": sim_val.get("err"),
            "logs": sim_val.get("logs"),
        },
        "ix0_ata": dump_ix_accounts(ata_ix),
        "ix1_buy": dump_ix_accounts(buy_ix),
    }


def load_keypair_for_user(telegram_user_id: str) -> Keypair:
    return wallet_get_keypair(telegram_user_id)


def send_buy_tx(user_keypair: Keypair, mint_str: str, sol_in: float, slippage_pct: float):
    """
    Build and SEND a Pump.fun buy transaction.
    Returns {"plan": plan, "sig": signature, "tx": VersionedTransaction}.
    """
    import base64

    # 1) Build instructions + plan
    plan, buy_ix = build_buy_ix_and_plan(user_keypair, mint_str, sol_in, slippage_pct)

    # 2) Build ATA create (idempotent) for the user
    owner = user_keypair.pubkey()
    mint = Pubkey.from_string(mint_str)
    user_ata = Pubkey.from_string(str(plan.user_ata)) if hasattr(plan, "user_ata") else Pubkey.from_string(str(plan.user_token_account)) if hasattr(plan, "user_token_account") else None
    if user_ata is None:
        # fallback: compute ATA using spl helper already used elsewhere in this file (if present)
        try:
            from spl.token.instructions import get_associated_token_address
            user_ata = get_associated_token_address(owner, mint, Pubkey.from_string(str(plan.token_program)))
        except Exception as e:
            raise RuntimeError("Could not determine user ATA from plan") from e

    token_program = Pubkey.from_string(str(plan.token_program))
    ata_ix = ata_create_idempotent_ix(owner, owner, mint, user_ata, token_program)

    # 3) Build tx
    http = get_http_client()
    bh = rpc_get_latest_blockhash(http)
    # bh may be dict or str depending on your rpc helper
    if isinstance(bh, dict):
        # common shape: {"blockhash": "..."}
        bh = bh.get("blockhash") or bh.get("value", {}).get("blockhash")
    if not isinstance(bh, str):
        raise RuntimeError(f"Unexpected latest blockhash type: {type(bh)} -> {bh}")
    bh = bh.strip()
    msg = MessageV0.try_compile(owner, [ata_ix, buy_ix], [], Hash.from_string(bh))
    tx = VersionedTransaction(msg, [user_keypair])

    # 4) Send via RPC (base64 encoding)
    rpc_url = _rpc_url().strip()
    tx_b64 = base64.b64encode(bytes(tx)).decode("utf-8")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendTransaction",
        "params": [tx_b64, {"encoding":"base64","skipPreflight":True,"maxRetries":3,"preflightCommitment":"processed"}],
    }
    r = http.post(rpc_url, json=payload, timeout=30.0)
    j = r.json()
    if "error" in j:
        raise RuntimeError(f"sendTransaction error: {j['error']}")
    sig = j.get("result")
    return {"plan": plan, "sig": sig, "tx": tx}
