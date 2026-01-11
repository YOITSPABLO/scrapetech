import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

DEFAULT_DB_PATH = os.getenv("SCRAPETECH_DB", "scrapetech.db")

@contextmanager
def connect(db_path: str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id TEXT UNIQUE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            handle TEXT UNIQUE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, channel_id),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(channel_id) REFERENCES channels(id)
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            telegram_message_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(channel_id, telegram_message_id),
            FOREIGN KEY(channel_id) REFERENCES channels(id)
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            mint TEXT NOT NULL,
            confidence INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(channel_id) REFERENCES channels(id),
            FOREIGN KEY(message_id) REFERENCES messages(id)
        );
        """)


        conn.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            trade_mode TEXT NOT NULL DEFAULT 'normal',
            position_mode TEXT NOT NULL DEFAULT 'single',
            max_open_positions INTEGER NOT NULL DEFAULT 1,
            buy_amount_sol REAL NOT NULL DEFAULT 0.5,
            buy_slippage_pct REAL NOT NULL DEFAULT 20.0,
            sell_slippage_pct REAL NOT NULL DEFAULT 20.0,
            tp_sl_enabled INTEGER NOT NULL DEFAULT 1,
            take_profit_pct REAL NOT NULL DEFAULT 30.0,
            stop_loss_pct REAL NOT NULL DEFAULT 20.0,
            cooldown_seconds INTEGER NOT NULL DEFAULT 60,
            max_trades_per_day INTEGER NOT NULL DEFAULT 20,
            duplicate_mint_block INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_intents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            signal_id INTEGER NOT NULL,
            mint TEXT NOT NULL,
            intent_type TEXT NOT NULL DEFAULT 'AUTO_BUY',
            status TEXT NOT NULL DEFAULT 'PENDING',
            reason TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(channel_id) REFERENCES channels(id),
            FOREIGN KEY(signal_id) REFERENCES signals(id)
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS auto_mint_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            mint TEXT NOT NULL,
            first_auto_buy_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, mint),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """)

def smoke(db_path: str = DEFAULT_DB_PATH) -> None:
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO users (telegram_user_id) VALUES (?)", ("12345",))
        conn.execute("INSERT OR IGNORE INTO channels (handle) VALUES (?)", ("@thewhitetest",))

        user_id = conn.execute("SELECT id FROM users WHERE telegram_user_id=?", ("12345",)).fetchone()["id"]
        channel_id = conn.execute("SELECT id FROM channels WHERE handle=?", ("@thewhitetest",)).fetchone()["id"]

        conn.execute("""
        INSERT OR IGNORE INTO subscriptions (user_id, channel_id, status)
        VALUES (?, ?, 'ACTIVE')
        """, (user_id, channel_id))

        row = conn.execute("""
        SELECT u.telegram_user_id, c.handle, s.status
        FROM subscriptions s
        JOIN users u ON u.id = s.user_id
        JOIN channels c ON c.id = s.channel_id
        WHERE u.telegram_user_id=? AND c.handle=?
        """, ("12345", "@thewhitetest")).fetchone()

        if not row:
            raise RuntimeError("Smoke test failed")

        print(f"DB SMOKE OK: user={row['telegram_user_id']} channel={row['handle']} status={row['status']}")

def get_or_create_channel(handle: str, db_path: str = DEFAULT_DB_PATH) -> int:
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO channels (handle) VALUES (?)", (handle,))
        row = conn.execute("SELECT id FROM channels WHERE handle=?", (handle,)).fetchone()
        return int(row["id"])

def get_or_create_user(telegram_user_id: str, db_path: str = DEFAULT_DB_PATH) -> int:
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO users (telegram_user_id) VALUES (?)", (telegram_user_id,))
        row = conn.execute("SELECT id FROM users WHERE telegram_user_id=?", (telegram_user_id,)).fetchone()
        return int(row["id"])

def insert_message(channel_id: int, telegram_message_id: int, text: str, db_path: str = DEFAULT_DB_PATH) -> int:
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO messages (channel_id, telegram_message_id, text) VALUES (?, ?, ?)",
            (channel_id, telegram_message_id, text),
        )
        row = conn.execute(
            "SELECT id FROM messages WHERE channel_id=? AND telegram_message_id=?",
            (channel_id, telegram_message_id),
        ).fetchone()
        return int(row["id"])

def insert_signal(channel_id: int, message_id: int, mint: str, confidence: int, db_path: str = DEFAULT_DB_PATH) -> int:
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO signals (channel_id, message_id, mint, confidence) VALUES (?, ?, ?, ?)",
            (channel_id, message_id, mint, confidence),
        )
        return int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

def upsert_subscription(telegram_user_id: str, channel_handle: str, status: str = "ACTIVE", db_path: str = DEFAULT_DB_PATH) -> None:
    user_id = get_or_create_user(telegram_user_id, db_path=db_path)
    channel_id = get_or_create_channel(channel_handle, db_path=db_path)
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO subscriptions (user_id, channel_id, status)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, channel_id) DO UPDATE SET status=excluded.status
            """,
            (user_id, channel_id, status),
        )

def list_subscriptions(telegram_user_id: str, db_path: str = DEFAULT_DB_PATH):
    init_db(db_path)
    with connect(db_path) as conn:
        user = conn.execute("SELECT id FROM users WHERE telegram_user_id=?", (telegram_user_id,)).fetchone()
        if not user:
            return []
        rows = conn.execute(
            """
            SELECT c.handle, s.status, s.created_at
            FROM subscriptions s
            JOIN channels c ON c.id = s.channel_id
            WHERE s.user_id=?
            ORDER BY c.handle
            """,
            (user["id"],),
        ).fetchall()
        return rows

def active_subscribers_for_channel(channel_handle: str, db_path: str = DEFAULT_DB_PATH):
    init_db(db_path)
    with connect(db_path) as conn:
        chan = conn.execute("SELECT id FROM channels WHERE handle=?", (channel_handle,)).fetchone()
        if not chan:
            return []
        rows = conn.execute(
            """
            SELECT u.telegram_user_id
            FROM subscriptions s
            JOIN users u ON u.id = s.user_id
            WHERE s.channel_id=? AND s.status='ACTIVE'
            """,
            (chan["id"],),
        ).fetchall()
        return [r["telegram_user_id"] for r in rows]

def ensure_user_settings(telegram_user_id: str, db_path: str = DEFAULT_DB_PATH) -> None:
    user_id = get_or_create_user(telegram_user_id, db_path=db_path)
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))

def update_user_settings(telegram_user_id: str, updates: dict, db_path: str = DEFAULT_DB_PATH) -> None:
    ensure_user_settings(telegram_user_id, db_path=db_path)
    user_id = get_or_create_user(telegram_user_id, db_path=db_path)

    allowed = {
        "trade_mode","position_mode","max_open_positions",
        "buy_amount_sol","buy_slippage_pct","sell_slippage_pct",
        "tp_sl_enabled","take_profit_pct","stop_loss_pct",
        "cooldown_seconds","max_trades_per_day","duplicate_mint_block",
    }
    bad = [k for k in updates.keys() if k not in allowed]
    if bad:
        raise ValueError(f"Unknown settings keys: {bad}")

    sets = ", ".join([f"{k}=?" for k in updates.keys()])
    vals = list(updates.values())
    vals.append(user_id)

    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            f"UPDATE user_settings SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            vals,
        )

def get_user_settings(telegram_user_id: str, db_path: str = DEFAULT_DB_PATH):
    ensure_user_settings(telegram_user_id, db_path=db_path)
    init_db(db_path)
    with connect(db_path) as conn:
        user = conn.execute("SELECT id FROM users WHERE telegram_user_id=?", (telegram_user_id,)).fetchone()
        row = conn.execute("SELECT * FROM user_settings WHERE user_id=?", (user["id"],)).fetchone()
        return dict(row)

def insert_trade_intent(telegram_user_id: str, channel_handle: str, signal_id: int, mint: str,
                        intent_type: str = "AUTO_BUY", status: str = "PENDING", reason: str | None = None,
                        db_path: str = DEFAULT_DB_PATH) -> int:
    user_id = get_or_create_user(telegram_user_id, db_path=db_path)
    channel_id = get_or_create_channel(channel_handle, db_path=db_path)

    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO trade_intents (user_id, channel_id, signal_id, mint, intent_type, status, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, channel_id, signal_id, mint, intent_type, status, reason),
        )
        return int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

def tail_trade_intents(n: int = 20, db_path: str = DEFAULT_DB_PATH):
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT ti.id, u.telegram_user_id, c.handle, ti.mint, ti.intent_type, ti.status, ti.reason, ti.created_at
            FROM trade_intents ti
            JOIN users u ON u.id = ti.user_id
            JOIN channels c ON c.id = ti.channel_id
            ORDER BY ti.id DESC
            LIMIT ?
            """,
            (n,),
        ).fetchall()
        return [dict(r) for r in rows]
