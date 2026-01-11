from __future__ import annotations

from dataclasses import dataclass
import base64
import struct
from typing import Optional, Any

from solders.pubkey import Pubkey

from .solana_rpc import get_client


# Pump.fun program id (mainnet) per Solscan
# https://solscan.io/account/6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P
PUMP_PROGRAM_ID = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")


@dataclass(frozen=True)
class BondingCurveState:
    virtual_token_reserves: int
    virtual_sol_reserves: int
    real_token_reserves: int
    real_sol_reserves: int
    token_total_supply: int
    complete: bool
    creator: str


@dataclass(frozen=True)
class PumpQuoteBuy:
    mint: str
    route: str  # "pumpfun"
    sol_in: float
    lamports_in: int
    fee_lamports: int
    lamports_in_after_fee: int
    est_tokens_out_raw: int
    est_price_sol_per_token: Optional[float]
    curve_complete: bool
    curve_pda: str
    creator: str


def _extract_base64_from_rpc_value(val_data: Any) -> str:
    """
    Normalizes Solana RPC account 'data' across providers:
      - [base64, "base64"]
      - [[base64], "base64"]
      - [base64]
      - etc.
    """
    raw = val_data
    if isinstance(raw, (list, tuple)):
        if len(raw) == 2 and isinstance(raw[0], (str, bytes)):
            data_b64 = raw[0]
        elif len(raw) == 2 and isinstance(raw[0], (list, tuple)) and raw[0]:
            data_b64 = raw[0][0]
        elif len(raw) >= 1:
            data_b64 = raw[0]
        else:
            raise ValueError("RPC returned empty account data array")
    else:
        data_b64 = raw

    # Some RPCs may return raw bytes instead of base64 strings
    if isinstance(data_b64, bytes):
        # If it's already binary account data, return a special marker by raising to caller
        # Caller will treat bytes as raw account data.
        return data_b64

    if not isinstance(data_b64, str):
        raise ValueError(f"RPC account data not base64 string/bytes (type={type(data_b64)})")

    return data_b64


def _get_account_data(pubkey: Pubkey) -> bytes:
    c = get_client()
    resp = c.get_account_info(pubkey, encoding="base64")
    val = resp.value
    if val is None:
        raise ValueError(f"Account not found: {str(pubkey)}")

    data_or_b64 = _extract_base64_from_rpc_value(val.data)
    if isinstance(data_or_b64, (bytes, bytearray)):
        return bytes(data_or_b64)
    try:
        return base64.b64decode(data_or_b64)
    except Exception as e:
        # Some providers may return base64+zstd/binary for some accounts.
        raise ValueError("RPC returned non-plain-base64 account data for Pump account") from e


def get_bonding_curve_pda(mint: str) -> Pubkey:
    mint_pk = Pubkey.from_string(mint)
    # Seeds per pump public docs: "bonding-curve" + mint + PUMP_PROGRAM_ID
    pda, _bump = Pubkey.find_program_address(
        [b"bonding-curve", bytes(mint_pk)],
        PUMP_PROGRAM_ID,
    )
    return pda


def decode_bonding_curve_state(account_data: bytes) -> BondingCurveState:
    """
    Anchor accounts start with 8-byte discriminator, followed by borsh fields.
    Field order for BondingCurve per generated IDL bindings:
      u64 vToken
      u64 vSol
      u64 realToken
      u64 realSol
      u64 tokenTotalSupply
      bool complete
      pubkey creator (32 bytes)
    """
    if len(account_data) < 8 + 8 * 5 + 1 + 32:
        raise ValueError(f"Bonding curve account too small: {len(account_data)} bytes")

    off = 8  # skip discriminator
    v_token, v_sol, r_token, r_sol, total_supply = struct.unpack_from("<QQQQQ", account_data, off)
    off += 8 * 5

    complete = struct.unpack_from("<?", account_data, off)[0]
    off += 1

    creator_bytes = account_data[off : off + 32]
    creator = str(Pubkey.from_bytes(creator_bytes))

    return BondingCurveState(
        virtual_token_reserves=v_token,
        virtual_sol_reserves=v_sol,
        real_token_reserves=r_token,
        real_sol_reserves=r_sol,
        token_total_supply=total_supply,
        complete=bool(complete),
        creator=creator,
    )


def quote_buy_pumpfun(mint: str, sol_in: float, fee_bps: int = 0) -> PumpQuoteBuy:
    """
    Quote buy using a constant-product approximation against *virtual* reserves:
      tokens_out ~= (x * v_token) / (v_sol + x)
    where x = lamports_in_after_fee.

    NOTE: fee_bps default 0 for now; later we can fetch pump Global and apply real fee.
    """
    lamports_in = int(sol_in * 1_000_000_000)
    fee_lamports = (lamports_in * int(fee_bps)) // 10_000
    x = max(0, lamports_in - fee_lamports)

    curve_pda = get_bonding_curve_pda(mint)
    data = _get_account_data(curve_pda)
    st = decode_bonding_curve_state(data)

    if st.virtual_sol_reserves <= 0 or st.virtual_token_reserves <= 0 or x <= 0:
        est_out = 0
    else:
        # integer math
        est_out = (x * st.virtual_token_reserves) // (st.virtual_sol_reserves + x)

    # optional price estimate (SOL per token) based on virtual reserves
    price = None
    if st.virtual_token_reserves > 0 and st.virtual_sol_reserves > 0:
        price = (st.virtual_sol_reserves / 1_000_000_000) / st.virtual_token_reserves

    return PumpQuoteBuy(
        mint=mint,
        route="pumpfun",
        sol_in=sol_in,
        lamports_in=lamports_in,
        fee_lamports=fee_lamports,
        lamports_in_after_fee=x,
        est_tokens_out_raw=int(est_out),
        est_price_sol_per_token=price,
        curve_complete=st.complete,
        curve_pda=str(curve_pda),
        creator=st.creator,
    )
