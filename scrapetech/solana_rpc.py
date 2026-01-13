import os
from dataclasses import dataclass
from typing import Optional, Tuple

from solana.rpc.api import Client
from solders.pubkey import Pubkey

LAMPORTS_PER_SOL = 1_000_000_000
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM_ID = "TokenzQdYmf8s5w1Qx9r1v6A9F9Fj7X1n1rX5Yp4sQj"

def get_rpc_url() -> str:
    url = os.getenv("SOLANA_RPC_URL", "").strip()
    if not url:
        # Works for dev checks, but you should set your own RPC for reliability.
        url = "https://api.mainnet-beta.solana.com"
    return url

def rpc_client() -> Client:
    return Client(get_rpc_url())


def get_client() -> Client:
    # Backwards-compatible alias used by quote modules
    return rpc_client()

def sol_balance(pubkey_str: str) -> Tuple[int, float]:
    c = rpc_client()
    pub = Pubkey.from_string(pubkey_str)
    resp = c.get_balance(pub)
    lamports = int(resp.value)
    return lamports, lamports / LAMPORTS_PER_SOL

def latest_blockhash() -> str:
    c = rpc_client()
    resp = c.get_latest_blockhash()
    return str(resp.value.blockhash)

@dataclass(frozen=True)
class MintInfo:
    mint: str
    exists: bool
    owner: Optional[str]
    decimals: Optional[int]
    supply: Optional[int]

def _decode_mint_decimals_and_supply(data: bytes) -> Tuple[int, int]:
    """
    SPL Mint layout (base): decimals at byte 44, supply u64 at bytes 36..44 (LE).
    This is for classic SPL Token program (Tokenkeg...).
    Token-2022 has a different owner and may have extensions (but the base fields still exist).
    """
    if len(data) < 82:
        raise ValueError(f"Mint data too short: {len(data)} bytes")
    supply = int.from_bytes(data[36:44], "little", signed=False)
    decimals = int(data[44])
    return decimals, supply

def fetch_mint_info(mint_str: str) -> MintInfo:
    c = rpc_client()
    mint = Pubkey.from_string(mint_str)

    resp = c.get_account_info(mint, encoding="base64", data_slice=None)
    val = resp.value
    if val is None:
        return MintInfo(mint=mint_str, exists=False, owner=None, decimals=None, supply=None)

    owner = str(val.owner)
    # Helius and some RPCs wrap base64 differently
    raw = val.data
    if isinstance(raw, (list, tuple)):
        if len(raw) == 2 and isinstance(raw[0], (str, bytes)):
            data_b64 = raw[0]
        elif len(raw) == 2 and isinstance(raw[0], (list, tuple)):
            data_b64 = raw[0][0]
        elif len(raw) == 1:
            data_b64 = raw[0]
        else:
            data_b64 = raw[0]
    else:
        data_b64 = raw

    import base64
    if isinstance(data_b64, (list, tuple)):
        data_b64 = data_b64[0]
    if not isinstance(data_b64, (str, bytes)):
        raise ValueError(f"Unsupported account data encoding: {type(data_b64)}")
    try:
        data = base64.b64decode(data_b64)
    except Exception as e:
        # Some RPCs (e.g. Helius) may return base64+zstd/binary in certain wrappers.
        # Return partial info instead of crashing.
        return MintInfo(mint=mint_str, exists=True, owner=owner, decimals=None, supply=None)

    decimals = None
    supply = None
    try:
        decimals, supply = _decode_mint_decimals_and_supply(data)
    except Exception:
        # Not a classic mint layout; still return owner + existence
        pass

    return MintInfo(mint=mint_str, exists=True, owner=owner, decimals=decimals, supply=supply)


def get_account_data_bytes(pubkey_str: str) -> bytes | None:
    """
    Returns raw account data bytes for any pubkey, or None if account not found.
    Tries to be tolerant of RPC provider encoding.
    """
    from solders.pubkey import Pubkey
    import base64

    c = rpc_client()
    pk = Pubkey.from_string(pubkey_str)
    resp = c.get_account_info(pk, encoding="base64")
    val = resp.value
    if val is None:
        return None

    raw = val.data
    # Normalize:
    # - [base64, "base64"]
    # - [[base64], "base64"]
    # - [base64]
    # - bytes (already decoded)
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)

    if isinstance(raw, (list, tuple)):
        if len(raw) == 2 and isinstance(raw[0], (str, bytes)):
            data_b64 = raw[0]
        elif len(raw) == 2 and isinstance(raw[0], (list, tuple)) and raw[0]:
            data_b64 = raw[0][0]
        elif len(raw) >= 1:
            data_b64 = raw[0]
        else:
            return None
    else:
        data_b64 = raw

    if isinstance(data_b64, (bytes, bytearray)):
        # could be raw bytes already
        return bytes(data_b64)

    if not isinstance(data_b64, str):
        return None

    try:
        return base64.b64decode(data_b64)
    except Exception:
        # Provider returned base64+zstd/binary or other encoding; we won't crash.
        return None


def try_get_mint_decimals(mint_str: str) -> int | None:
    data = get_account_data_bytes(mint_str)
    if not data or len(data) < 45:
        return None
    # SPL mint decimals is at byte 44 (after mint authority option + key + supply)
    # This holds for classic mint layout; token-2022 base region keeps it in same spot.
    return int(data[44])

# -----------------------------
# Added helpers for pump_tx.py
# -----------------------------
import os
import httpx
from typing import List, Optional, Any, Dict

def get_http_client() -> httpx.Client:
    """
    Shared synchronous httpx client for JSON-RPC calls.
    """
    return httpx.Client(timeout=30.0)

def _rpc_url() -> str:
    url = os.getenv("SOLANA_RPC_URL", "").strip()
    if not url:
        raise ValueError("SOLANA_RPC_URL not set")
    return url

def rpc_get_latest_blockhash(client: httpx.Client) -> str:
    r = client.post(
        _rpc_url(),
        json={"jsonrpc": "2.0", "id": 1, "method": "getLatestBlockhash", "params": [{"commitment": "processed"}]},
    )
    r.raise_for_status()
    j = r.json()
    return j["result"]["value"]["blockhash"]

def rpc_get_multiple_accounts(client: httpx.Client, pubkeys: List[str]) -> List[Optional[Dict[str, Any]]]:
    """
    Returns list of account objects or None for missing accounts.
    Uses base64 encoding.
    """
    r = client.post(
        _rpc_url(),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getMultipleAccounts",
            "params": [pubkeys, {"encoding": "base64"}],
        },
    )
    r.raise_for_status()
    j = r.json()
    vals = j["result"]["value"]
    out: List[Optional[Dict[str, Any]]] = []
    for v in vals:
        out.append(v)  # v is dict or None
    return out

def rpc_get_transaction(client: httpx.Client, signature: str) -> Dict[str, Any] | None:
    r = client.post(
        _rpc_url(),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        },
    )
    r.raise_for_status()
    j = r.json()
    return j.get("result")

def rpc_get_signature_status(client: httpx.Client, signature: str) -> Dict[str, Any] | None:
    r = client.post(
        _rpc_url(),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignatureStatuses",
            "params": [[signature], {"searchTransactionHistory": True}],
        },
    )
    r.raise_for_status()
    j = r.json()
    vals = j.get("result", {}).get("value") or []
    return vals[0] if vals else None

def rpc_get_token_balance_for_owner_mint(
    client: httpx.Client, owner_pubkey: str, mint: str
) -> float | None:
    r = client.post(
        _rpc_url(),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                owner_pubkey,
                {"mint": mint},
                {"encoding": "jsonParsed"},
            ],
        },
    )
    r.raise_for_status()
    j = r.json()
    vals = j.get("result", {}).get("value") or []
    total = 0.0
    for v in vals:
        data = v.get("account", {}).get("data", {})
        parsed = data.get("parsed", {})
        info = parsed.get("info", {})
        tok = info.get("tokenAmount", {})
        try:
            ui_amt = tok.get("uiAmount")
            if ui_amt is None:
                ui_amt = tok.get("uiAmountString")
            amt = float(ui_amt or 0.0)
        except Exception:
            amt = 0.0
        total += amt
    return total

def rpc_get_token_accounts_by_owner(client: httpx.Client, owner_pubkey: str) -> list[dict]:
    def _fetch(program_id: str) -> list[dict]:
        r = client.post(
            _rpc_url(),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [
                    owner_pubkey,
                    {"programId": program_id},
                    {"encoding": "jsonParsed"},
                ],
            },
        )
        r.raise_for_status()
        j = r.json()
        return j.get("result", {}).get("value") or []

    accounts = []
    for pid in (TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID):
        try:
            accounts.extend(_fetch(pid))
        except Exception:
            continue

    totals: dict[str, dict] = {}
    for v in accounts:
        data = v.get("account", {}).get("data", {})
        parsed = data.get("parsed", {})
        info = parsed.get("info", {})
        mint = info.get("mint")
        tok = info.get("tokenAmount", {})
        try:
            decimals = int(tok.get("decimals") or 0)
        except Exception:
            decimals = 0
        try:
            ui_amt = tok.get("uiAmount")
            if ui_amt is None:
                ui_amt = tok.get("uiAmountString")
            if ui_amt is None:
                raw = tok.get("amount")
                if raw is not None:
                    ui_amt = float(raw) / (10 ** int(decimals))
            amt = float(ui_amt or 0.0)
        except Exception:
            amt = 0.0
        if not mint:
            continue
        cur = totals.get(mint)
        if not cur:
            totals[mint] = {"mint": mint, "ui_amount": amt, "decimals": decimals}
        else:
            cur["ui_amount"] += amt

    return sorted(totals.values(), key=lambda x: x["ui_amount"], reverse=True)

def rpc_get_assets_by_owner(client: httpx.Client, owner_pubkey: str, page: int = 1, limit: int = 200) -> list[dict]:
    r = client.post(
        _rpc_url(),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAssetsByOwner",
            "params": {
                "ownerAddress": owner_pubkey,
                "page": int(page),
                "limit": int(limit),
                "displayOptions": {"showFungible": True},
            },
        },
    )
    r.raise_for_status()
    j = r.json()
    items = j.get("result", {}).get("items") or []
    out = []
    for it in items:
        if it.get("interface") != "FungibleToken":
            continue
        token = it.get("token_info") or {}
        mint = it.get("id")
        decimals = int(token.get("decimals") or 0)
        raw_bal = token.get("balance")
        ui_amt = None
        try:
            if raw_bal is not None:
                ui_amt = float(raw_bal) / (10 ** int(decimals))
        except Exception:
            ui_amt = None
        if mint and ui_amt is not None:
            out.append({"mint": mint, "ui_amount": ui_amt, "decimals": decimals})
    return sorted(out, key=lambda x: x["ui_amount"], reverse=True)

def rpc_get_token_balance_for_owner_mint_any(
    client: httpx.Client, owner_pubkey: str, mint: str
) -> float | None:
    try:
        bal = rpc_get_token_balance_for_owner_mint(client, owner_pubkey, mint)
        if bal and bal > 0:
            return bal
    except Exception:
        pass
    try:
        assets = rpc_get_assets_by_owner(client, owner_pubkey)
        for it in assets:
            if it.get("mint") == mint:
                return float(it.get("ui_amount") or 0.0)
    except Exception:
        pass
    return None

def extract_tx_deltas(tx: Dict[str, Any], owner_pubkey: str, mint: str) -> Dict[str, Any]:
    if not tx or "meta" not in tx or "transaction" not in tx:
        return {}

    meta = tx["meta"] or {}
    msg = tx["transaction"].get("message") or {}
    keys = msg.get("accountKeys") or []

    owner_index = None
    for i, k in enumerate(keys):
        if isinstance(k, dict):
            pk = k.get("pubkey")
        else:
            pk = k
        if pk == owner_pubkey:
            owner_index = i
            break

    sol_delta_lamports = None
    if owner_index is not None:
        pre = meta.get("preBalances") or []
        post = meta.get("postBalances") or []
        if owner_index < len(pre) and owner_index < len(post):
            sol_delta_lamports = int(post[owner_index]) - int(pre[owner_index])

    def _sum_amounts(items):
        total_raw = 0
        decimals = None
        for it in items or []:
            if it.get("owner") == owner_pubkey and it.get("mint") == mint:
                ui = it.get("uiTokenAmount") or {}
                total_raw += int(ui.get("amount") or 0)
                if decimals is None:
                    try:
                        decimals = int(ui.get("decimals") or 0)
                    except Exception:
                        decimals = None
        return total_raw, decimals

    pre_raw, pre_decimals = _sum_amounts(meta.get("preTokenBalances"))
    post_raw, post_decimals = _sum_amounts(meta.get("postTokenBalances"))

    decimals = post_decimals if post_decimals is not None else pre_decimals

    token_delta_raw = int(post_raw) - int(pre_raw)
    token_delta_ui = None
    if decimals is not None:
        token_delta_ui = token_delta_raw / (10 ** int(decimals))

    return {
        "sol_delta_lamports": sol_delta_lamports,
        "token_delta_raw": token_delta_raw,
        "token_delta_ui": token_delta_ui,
        "token_decimals": decimals,
    }
