from __future__ import annotations

import base64
import struct
from dataclasses import dataclass
from typing import Any, Dict, Tuple

from solders.pubkey import Pubkey
from solders.instruction import Instruction, AccountMeta
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.transaction import VersionedTransaction

from .solana_rpc import get_http_client, rpc_get_latest_blockhash, rpc_get_multiple_accounts, _rpc_url
from .pump_quotes import decode_bonding_curve_state, get_bonding_curve_pda

PUMPFUN_PROGRAM_ID = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
EVENT_AUTHORITY = Pubkey.from_string("Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1")
SYSTEM_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")
ASSOCIATED_TOKEN_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")

GLOBAL = Pubkey.from_string("4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf")
FEE_RECIPIENT = Pubkey.from_string("CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM")
FEE_CONFIG = Pubkey.from_string("8Wf5TiAheLUqBrKXeYg2JtAFFMWtKdG2BSFgqUcPVwTt")
FEE_PROGRAM = Pubkey.from_string("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ")
GLOBAL_VOLUME = Pubkey.from_string("Hq2wp8uJ9jCPsYgNHex8RtqdvMPfVGoYwjvF1ATiwn2Y")

SELL_DISCRIMINATOR = bytes([51, 230, 133, 164, 1, 127, 131, 173])

GLOBAL_SEED = b"global"
BONDING_SEED = b"bonding-curve"

@dataclass(frozen=True)
class SellPlan:
    user_pubkey: str
    mint: str
    tokens_to_sell_raw: int
    min_sol_output_lamports: int
    token_program: str
    bonding_curve: str
    curve_ata: str
    user_ata: str
    fee_recipient: str

def _pda(seed_bytes: list[bytes], program_id: Pubkey) -> Pubkey:
    return Pubkey.find_program_address(seed_bytes, program_id)[0]

def _get_global_pda() -> Pubkey:
    return _pda([GLOBAL_SEED], PUMPFUN_PROGRAM_ID)

def _get_bonding_curve_pda(mint: Pubkey) -> Pubkey:
    return _pda([BONDING_SEED, bytes(mint)], PUMPFUN_PROGRAM_ID)

def _get_associated_token_address(owner: Pubkey, mint: Pubkey, token_program: Pubkey) -> Pubkey:
    return Pubkey.find_program_address(
        [bytes(owner), bytes(token_program), bytes(mint)],
        ASSOCIATED_TOKEN_PROGRAM,
    )[0]

def _get_user_volume_accumulator(owner: Pubkey) -> Pubkey:
    return Pubkey.find_program_address([b"user_volume_accumulator", bytes(owner)], PUMPFUN_PROGRAM_ID)[0]

def _get_creator_vault_pda(creator: Pubkey) -> Pubkey:
    return Pubkey.find_program_address([b"creator-vault", bytes(creator)], PUMPFUN_PROGRAM_ID)[0]

def _get_account_data(pubkey: Pubkey) -> bytes:
    http = get_http_client()
    vals = rpc_get_multiple_accounts(http, [str(pubkey)])
    acct = vals[0] if vals else None
    if not acct or "data" not in acct:
        raise RuntimeError(f"Account not found: {str(pubkey)}")
    data = acct.get("data")
    if isinstance(data, list) and data:
        data_b64 = data[0]
    elif isinstance(data, str):
        data_b64 = data
    else:
        raise RuntimeError("Account data in unexpected format")
    return base64.b64decode(data_b64)

def _fetch_mint_owner_program(mint: Pubkey) -> Pubkey:
    http = get_http_client()
    vals = rpc_get_multiple_accounts(http, [str(mint)])
    acct = vals[0] if vals else None
    if not acct:
        raise RuntimeError("Could not fetch mint account")
    owner = acct.get("owner")
    if not owner:
        raise RuntimeError("Mint fetch did not include owner")
    return Pubkey.from_string(owner)

def build_sell_ix_and_plan(
    *,
    user_keypair: Keypair,
    mint_str: str,
    tokens_to_sell_raw: int,
    min_sol_output_lamports: int = 1,
    **_ignored: Any,
) -> Tuple[SellPlan, Instruction]:
    if int(tokens_to_sell_raw) <= 0:
        raise ValueError("tokens_to_sell_raw must be positive")

    mint = Pubkey.from_string(mint_str)
    token_program = _fetch_mint_owner_program(mint)

    global_pda = GLOBAL
    bonding_curve = Pubkey.from_string(str(get_bonding_curve_pda(mint_str)))

    user_pubkey = user_keypair.pubkey()
    curve_ata = _get_associated_token_address(bonding_curve, mint, token_program)
    user_ata = _get_associated_token_address(user_pubkey, mint, token_program)

    curve_data = _get_account_data(bonding_curve)
    curve_state = decode_bonding_curve_state(curve_data)
    creator = Pubkey.from_string(curve_state.creator)
    creator_vault = _get_creator_vault_pda(creator)
    uva = _get_user_volume_accumulator(user_pubkey)

    ix_data = SELL_DISCRIMINATOR + struct.pack("<QQ", int(tokens_to_sell_raw), int(min_sol_output_lamports))
    metas = [
        AccountMeta(pubkey=global_pda, is_signer=False, is_writable=False),
        AccountMeta(pubkey=FEE_RECIPIENT, is_signer=False, is_writable=True),
        AccountMeta(pubkey=mint, is_signer=False, is_writable=False),
        AccountMeta(pubkey=bonding_curve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=curve_ata, is_signer=False, is_writable=True),
        AccountMeta(pubkey=user_ata, is_signer=False, is_writable=True),
        AccountMeta(pubkey=user_pubkey, is_signer=True, is_writable=True),
        AccountMeta(pubkey=SYSTEM_PROGRAM, is_signer=False, is_writable=False),
        AccountMeta(pubkey=creator_vault, is_signer=False, is_writable=True),
        AccountMeta(pubkey=token_program, is_signer=False, is_writable=False),
        AccountMeta(pubkey=EVENT_AUTHORITY, is_signer=False, is_writable=False),
        AccountMeta(pubkey=PUMPFUN_PROGRAM_ID, is_signer=False, is_writable=False),
        AccountMeta(pubkey=FEE_CONFIG, is_signer=False, is_writable=False),
        AccountMeta(pubkey=FEE_PROGRAM, is_signer=False, is_writable=False),
        AccountMeta(pubkey=GLOBAL_VOLUME, is_signer=False, is_writable=False),
        AccountMeta(pubkey=uva, is_signer=False, is_writable=True),
    ]

    ix = Instruction(program_id=PUMPFUN_PROGRAM_ID, data=ix_data, accounts=metas)
    plan = SellPlan(
        user_pubkey=str(user_pubkey),
        mint=mint_str,
        tokens_to_sell_raw=int(tokens_to_sell_raw),
        min_sol_output_lamports=int(min_sol_output_lamports),
        token_program=str(token_program),
        bonding_curve=str(bonding_curve),
        curve_ata=str(curve_ata),
        user_ata=str(user_ata),
        fee_recipient=str(FEE_RECIPIENT),
    )
    return plan, ix

def build_and_simulate_sell_tx(*, user_keypair: Keypair, sell_ix: Instruction) -> Dict[str, Any]:
    http = get_http_client()
    bh = rpc_get_latest_blockhash(http)

    msg = MessageV0.try_compile(
        payer=user_keypair.pubkey(),
        instructions=[sell_ix],
        address_lookup_table_accounts=[],
        recent_blockhash=Hash.from_string(bh),
    )
    tx = VersionedTransaction(msg, [user_keypair])

    r = http.post(
        _rpc_url(),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "simulateTransaction",
            "params": [
                base64.b64encode(bytes(tx)).decode("utf-8"),
                {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True},
            ],
        },
        timeout=30.0,
    )
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise RuntimeError(f"simulateTransaction error: {j['error']}")
    return j.get("result", {}).get("value", {})

def send_sell_tx(*, user_keypair: Keypair, sell_ix: Instruction, skip_preflight: bool = True) -> str:
    http = get_http_client()
    bh = rpc_get_latest_blockhash(http)

    msg = MessageV0.try_compile(
        payer=user_keypair.pubkey(),
        instructions=[sell_ix],
        address_lookup_table_accounts=[],
        recent_blockhash=Hash.from_string(bh),
    )
    tx = VersionedTransaction(msg, [user_keypair])

    tx_b64 = base64.b64encode(bytes(tx)).decode("utf-8")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendTransaction",
        "params": [
            tx_b64,
            {"encoding": "base64", "skipPreflight": bool(skip_preflight), "maxRetries": 3},
        ],
    }
    r = http.post(_rpc_url(), json=payload, timeout=30.0)
    j = r.json()
    if "error" in j:
        raise RuntimeError(f"sendTransaction error: {j['error']}")
    return j.get("result")
