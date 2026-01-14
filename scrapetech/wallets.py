import os
import base64
from dataclasses import dataclass
from typing import Optional, Tuple, List

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.fernet import Fernet
from nacl.signing import SigningKey
import base58

from .db import init_db, connect, get_or_create_user

def _derive_fernet_key(password: str, salt: bytes) -> bytes:
    # PBKDF2 -> 32 bytes -> base64 urlsafe for Fernet
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=200_000,
    )
    key = kdf.derive(password.encode("utf-8"))
    return base64.urlsafe_b64encode(key)

def _get_password() -> str:
    pw = os.getenv("SCRAPETECH_WALLET_PASSWORD", "").strip()
    if not pw:
        raise ValueError("Missing SCRAPETECH_WALLET_PASSWORD env var (do not put passwords in shell history)")
    if len(pw) < 8:
        raise ValueError("Password too short (min 8 chars)")
    return pw

def _pubkey_from_signing_key(sk: SigningKey) -> str:
    vk = sk.verify_key.encode()  # 32 bytes
    return base58.b58encode(vk).decode("utf-8")

def _parse_secret(secret: str) -> bytes:
    """
    Accepts:
      - base58 encoded 32-byte seed OR 64-byte secret key
      - JSON-like: [1,2,3,...] (32 or 64 ints)
    Returns seed32 bytes.
    """
    secret = secret.strip()

    # JSON array form
    if secret.startswith("[") and secret.endswith("]"):
        parts = secret.strip("[]").split(",")
        raw = bytes(int(x.strip()) for x in parts if x.strip() != "")
    else:
        # base58
        raw = base58.b58decode(secret)

    if len(raw) == 32:
        return raw
    if len(raw) == 64:
        return raw[:32]
    raise ValueError(f"Secret must decode to 32 or 64 bytes, got {len(raw)}")

@dataclass(frozen=True)
class WalletRecord:
    id: int
    name: str
    pubkey: str
    is_default: bool

def _next_wallet_name(conn, user_id: int) -> str:
    row = conn.execute("SELECT COUNT(1) AS n FROM wallet_accounts WHERE user_id=?", (user_id,)).fetchone()
    idx = int(row["n"]) + 1 if row else 1
    return f"Wallet {idx}"

def _ensure_default_wallet(conn, user_id: int) -> None:
    row = conn.execute(
        "SELECT id FROM wallet_accounts WHERE user_id=? AND is_default=1",
        (user_id,),
    ).fetchone()
    if row:
        return
    row = conn.execute(
        "SELECT id FROM wallet_accounts WHERE user_id=? ORDER BY id ASC LIMIT 1",
        (user_id,),
    ).fetchone()
    if row:
        conn.execute("UPDATE wallet_accounts SET is_default=1 WHERE id=?", (row["id"],))

def _migrate_wallets_if_needed(conn, user_id: int) -> None:
    # If the new table is empty for this user but old table has data, migrate once.
    row = conn.execute(
        "SELECT 1 FROM wallet_accounts WHERE user_id=? LIMIT 1",
        (user_id,),
    ).fetchone()
    if row:
        return
    old = conn.execute(
        "SELECT pubkey, enc_secret, salt FROM wallets WHERE user_id=?",
        (user_id,),
    ).fetchone()
    if not old:
        return
    conn.execute(
        """
        INSERT INTO wallet_accounts (user_id, name, pubkey, enc_secret, salt, is_default)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (user_id, "Wallet 1", old["pubkey"], old["enc_secret"], old["salt"]),
    )

def wallet_create(telegram_user_id: str, name: Optional[str] = None, make_default: Optional[bool] = None):
    """
    Creates a new keypair, encrypts seed, stores it.

    Returns a dict of exports:
      - pubkey
      - seed_base58 (32 bytes)
      - phantom_secret_base58 (64 bytes = seed||pubkey)
      - phantom_secret_json (list of 64 ints)
    """
    pw = _get_password()
    user_id = get_or_create_user(telegram_user_id)

    init_db()
    seed = SigningKey.generate().encode()  # 32-byte seed
    sk = SigningKey(seed)
    pubkey = _pubkey_from_signing_key(sk)

    salt = os.urandom(16)
    f = Fernet(_derive_fernet_key(pw, salt))
    enc = f.encrypt(seed)

    with connect() as conn:
        _migrate_wallets_if_needed(conn, user_id)
        if not name:
            name = _next_wallet_name(conn, user_id)
        is_default = 1 if make_default or not conn.execute(
            "SELECT 1 FROM wallet_accounts WHERE user_id=? AND is_default=1",
            (user_id,),
        ).fetchone() else 0
        conn.execute(
            """
            INSERT INTO wallet_accounts (user_id, name, pubkey, enc_secret, salt, is_default)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, name, pubkey, enc, salt, is_default),
        )
        if is_default:
            conn.execute(
                "UPDATE wallet_accounts SET is_default=0 WHERE user_id=? AND name!=?",
                (user_id, name),
            )

    seed_b58 = base58.b58encode(seed).decode("utf-8")

    # Phantom-compatible "secret key" (64 bytes): seed + public key bytes
    pub_bytes = sk.verify_key.encode()
    secret64 = seed + pub_bytes
    phantom_b58 = base58.b58encode(secret64).decode("utf-8")
    phantom_json = list(secret64)

    return {
        "name": name,
        "is_default": bool(is_default),
        "pubkey": pubkey,
        "seed_base58": seed_b58,
        "phantom_secret_base58": phantom_b58,
        "phantom_secret_json": phantom_json,
    }

def wallet_import(
    telegram_user_id: str,
    secret: str,
    name: Optional[str] = None,
    make_default: Optional[bool] = None,
) -> WalletRecord:
    pw = _get_password()
    user_id = get_or_create_user(telegram_user_id)

    init_db()
    seed = _parse_secret(secret)
    sk = SigningKey(seed)
    pubkey = _pubkey_from_signing_key(sk)

    salt = os.urandom(16)
    f = Fernet(_derive_fernet_key(pw, salt))
    enc = f.encrypt(seed)

    with connect() as conn:
        _migrate_wallets_if_needed(conn, user_id)
        if not name:
            name = _next_wallet_name(conn, user_id)
        is_default = 1 if make_default or not conn.execute(
            "SELECT 1 FROM wallet_accounts WHERE user_id=? AND is_default=1",
            (user_id,),
        ).fetchone() else 0
        cur = conn.execute(
            """
            INSERT INTO wallet_accounts (user_id, name, pubkey, enc_secret, salt, is_default)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, name, pubkey, enc, salt, is_default),
        )
        wallet_id = cur.lastrowid
        if is_default:
            conn.execute(
                "UPDATE wallet_accounts SET is_default=0 WHERE user_id=? AND name!=?",
                (user_id, name),
            )
    return WalletRecord(id=wallet_id, name=name, pubkey=pubkey, is_default=bool(is_default))

def wallet_get_pubkey(telegram_user_id: str) -> Optional[str]:
    user_id = get_or_create_user(telegram_user_id)
    init_db()
    with connect() as conn:
        _migrate_wallets_if_needed(conn, user_id)
        row = conn.execute(
            "SELECT pubkey FROM wallet_accounts WHERE user_id=? AND is_default=1",
            (user_id,),
        ).fetchone()
        if row:
            return row["pubkey"]
        row = conn.execute(
            "SELECT pubkey FROM wallet_accounts WHERE user_id=? ORDER BY id ASC LIMIT 1",
            (user_id,),
        ).fetchone()
        return row["pubkey"] if row else None


from solders.keypair import Keypair

def wallet_get_keypair(telegram_user_id: str, wallet_id: Optional[int] = None) -> Keypair:
    """
    Decrypts the stored wallet seed and returns a Solders Keypair
    """
    pw = _get_password()
    user_id = get_or_create_user(telegram_user_id)

    init_db()
    with connect() as conn:
        _migrate_wallets_if_needed(conn, user_id)
        if wallet_id is None:
            row = conn.execute(
                "SELECT enc_secret, salt FROM wallet_accounts WHERE user_id=? AND is_default=1",
                (user_id,),
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT enc_secret, salt FROM wallet_accounts WHERE user_id=? ORDER BY id ASC LIMIT 1",
                    (user_id,),
                ).fetchone()
        else:
            row = conn.execute(
                "SELECT enc_secret, salt FROM wallet_accounts WHERE user_id=? AND id=?",
                (user_id, wallet_id),
            ).fetchone()

    if not row:
        raise ValueError("Wallet not found for user")

    enc = row["enc_secret"]
    salt = row["salt"]

    f = Fernet(_derive_fernet_key(pw, salt))
    seed = f.decrypt(enc)   # 32 bytes

    sk = SigningKey(seed)
    # Use raw 32-byte pubkey bytes (matches wallet_create export)
    pub_bytes = sk.verify_key.encode()

    # Solana Keypair expects 64 bytes = seed + pubkey
    secret64 = seed + pub_bytes
    return Keypair.from_bytes(secret64)

def wallet_list(telegram_user_id: str) -> List[WalletRecord]:
    user_id = get_or_create_user(telegram_user_id)
    init_db()
    with connect() as conn:
        _migrate_wallets_if_needed(conn, user_id)
        rows = conn.execute(
            "SELECT id, name, pubkey, is_default FROM wallet_accounts WHERE user_id=? ORDER BY id ASC",
            (user_id,),
        ).fetchall()
        return [
            WalletRecord(
                id=int(r["id"]),
                name=r["name"],
                pubkey=r["pubkey"],
                is_default=bool(r["is_default"]),
            )
            for r in rows
        ]

def wallet_set_default(telegram_user_id: str, wallet_id: int) -> None:
    user_id = get_or_create_user(telegram_user_id)
    init_db()
    with connect() as conn:
        _migrate_wallets_if_needed(conn, user_id)
        row = conn.execute(
            "SELECT id FROM wallet_accounts WHERE user_id=? AND id=?",
            (user_id, wallet_id),
        ).fetchone()
        if not row:
            raise ValueError("Wallet not found for user")
        conn.execute("UPDATE wallet_accounts SET is_default=0 WHERE user_id=?", (user_id,))
        conn.execute("UPDATE wallet_accounts SET is_default=1 WHERE id=?", (wallet_id,))
