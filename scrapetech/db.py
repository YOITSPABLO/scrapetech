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
            status TEXT NOT NULL DEFAULT 'ACTIVE', -- ACTIVE | PAUSED | STOPPED | DELETED
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
            raise RuntimeError("Smoke test failed: no subscription row found")

        print(f"DB SMOKE OK: user={row['telegram_user_id']} channel={row['handle']} status={row['status']}")

def get_or_create_channel(handle: str, db_path: str = DEFAULT_DB_PATH) -> int:
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO channels (handle) VALUES (?)", (handle,))
        row = conn.execute("SELECT id FROM channels WHERE handle=?", (handle,)).fetchone()
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

def get_or_create_user(telegram_user_id: str, db_path: str = DEFAULT_DB_PATH) -> int:
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO users (telegram_user_id) VALUES (?)", (telegram_user_id,))
        row = conn.execute("SELECT id FROM users WHERE telegram_user_id=?", (telegram_user_id,)).fetchone()
        return int(row["id"])

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
