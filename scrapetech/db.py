import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

DEFAULT_DB_PATH = os.getenv("SCRAPETECH_DB", "scrapetech.db")

def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if any(c["name"] == column for c in cols):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl};")

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
            buy_amount_sol REAL NOT NULL DEFAULT 0.001,
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
        _ensure_column(conn, "user_settings", "auto_buy_enabled", "INTEGER NOT NULL DEFAULT 1")

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

        conn.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            pubkey TEXT NOT NULL,
            enc_secret BLOB NOT NULL,
            salt BLOB NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            mint TEXT NOT NULL,
            token_balance REAL NOT NULL DEFAULT 0,
            avg_entry_sol REAL NOT NULL DEFAULT 0,
            total_sol_spent REAL NOT NULL DEFAULT 0,
            total_sol_received REAL NOT NULL DEFAULT 0,
            realized_pnl_sol REAL NOT NULL DEFAULT 0,
            open INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, mint),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            mint TEXT NOT NULL,
            side TEXT NOT NULL,
            token_amount REAL NOT NULL,
            sol_amount REAL NOT NULL,
            price_sol_per_token REAL NOT NULL,
            tx_sig TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            mint TEXT NOT NULL,
            side TEXT NOT NULL,
            signature TEXT NOT NULL UNIQUE,
            requested_token_amount REAL,
            requested_sol_amount REAL,
            actual_token_amount REAL,
            actual_sol_amount REAL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            error TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
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

def get_telegram_user_id(user_id: int, db_path: str = DEFAULT_DB_PATH) -> str | None:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT telegram_user_id FROM users WHERE id=?", (int(user_id),)).fetchone()
        return str(row["telegram_user_id"]) if row else None

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
        "auto_buy_enabled",
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

def get_position(telegram_user_id: str, mint: str, db_path: str = DEFAULT_DB_PATH):
    user_id = get_or_create_user(telegram_user_id, db_path=db_path)
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM positions
            WHERE user_id=? AND mint=?
            """,
            (user_id, mint),
        ).fetchone()
        return dict(row) if row else None

def list_positions(telegram_user_id: str, db_path: str = DEFAULT_DB_PATH):
    user_id = get_or_create_user(telegram_user_id, db_path=db_path)
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM positions
            WHERE user_id=?
            ORDER BY mint
            """,
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

def apply_trade(
    telegram_user_id: str,
    mint: str,
    side: str,
    token_amount: float,
    sol_amount: float,
    tx_sig: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
):
    epsilon = 1e-9
    if token_amount <= 0 or sol_amount <= 0:
        raise ValueError("token_amount and sol_amount must be positive")

    side = side.strip().upper()
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")

    user_id = get_or_create_user(telegram_user_id, db_path=db_path)
    init_db(db_path)

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM positions
            WHERE user_id=? AND mint=?
            """,
            (user_id, mint),
        ).fetchone()

        token_balance = float(row["token_balance"]) if row else 0.0
        avg_entry = float(row["avg_entry_sol"]) if row else 0.0
        total_spent = float(row["total_sol_spent"]) if row else 0.0
        total_received = float(row["total_sol_received"]) if row else 0.0
        realized_pnl = float(row["realized_pnl_sol"]) if row else 0.0

        if side == "BUY":
            new_balance = token_balance + token_amount
            new_total_spent = total_spent + sol_amount
            new_avg_entry = new_total_spent / new_balance if new_balance > 0 else 0.0
            new_total_received = total_received
            new_realized_pnl = realized_pnl
            open_flag = 1
        else:
            if token_balance <= 0:
                raise ValueError("No open position to sell")
            if token_amount > token_balance:
                raise ValueError("Cannot sell more than current token balance")

            pnl = sol_amount - (token_amount * avg_entry)
            new_balance = token_balance - token_amount
            new_total_spent = total_spent
            new_total_received = total_received + sol_amount
            new_realized_pnl = realized_pnl + pnl
            if new_balance <= epsilon:
                new_balance = 0.0
                new_avg_entry = 0.0
                open_flag = 0
            else:
                new_avg_entry = avg_entry
                open_flag = 1

        price = sol_amount / token_amount
        conn.execute(
            """
            INSERT INTO trades (user_id, mint, side, token_amount, sol_amount, price_sol_per_token, tx_sig)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, mint, side, token_amount, sol_amount, price, tx_sig),
        )

        if row:
            conn.execute(
                """
                UPDATE positions
                SET token_balance=?, avg_entry_sol=?, total_sol_spent=?, total_sol_received=?,
                    realized_pnl_sol=?, open=?, updated_at=CURRENT_TIMESTAMP
                WHERE user_id=? AND mint=?
                """,
                (
                    new_balance, new_avg_entry, new_total_spent, new_total_received,
                    new_realized_pnl, open_flag, user_id, mint,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO positions (user_id, mint, token_balance, avg_entry_sol, total_sol_spent,
                                       total_sol_received, realized_pnl_sol, open)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, mint, new_balance, new_avg_entry, new_total_spent,
                    new_total_received, new_realized_pnl, open_flag,
                ),
            )

        row = conn.execute(
            "SELECT * FROM positions WHERE user_id=? AND mint=?",
            (user_id, mint),
        ).fetchone()
        return dict(row) if row else None

def enqueue_pending_trade(
    telegram_user_id: str,
    mint: str,
    side: str,
    signature: str,
    requested_token_amount: float | None = None,
    requested_sol_amount: float | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    side = side.strip().upper()
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")

    user_id = get_or_create_user(telegram_user_id, db_path=db_path)
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pending_trades (
                user_id, mint, side, signature, requested_token_amount, requested_sol_amount, status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'PENDING')
            ON CONFLICT(signature) DO UPDATE SET
                requested_token_amount=excluded.requested_token_amount,
                requested_sol_amount=excluded.requested_sol_amount,
                updated_at=CURRENT_TIMESTAMP
            """,
            (user_id, mint, side, signature, requested_token_amount, requested_sol_amount),
        )

def update_pending_trade_status(
    signature: str,
    status: str,
    error: str | None = None,
    actual_token_amount: float | None = None,
    actual_sol_amount: float | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    status = status.strip().upper()
    if status not in ("PENDING", "SUCCESS", "FAILED"):
        raise ValueError("status must be PENDING, SUCCESS, or FAILED")

    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE pending_trades
            SET status=?, error=?, actual_token_amount=?, actual_sol_amount=?, updated_at=CURRENT_TIMESTAMP
            WHERE signature=?
            """,
            (status, error, actual_token_amount, actual_sol_amount, signature),
        )

def list_pending_trades(status: str | None = "PENDING", limit: int = 50, db_path: str = DEFAULT_DB_PATH):
    init_db(db_path)
    with connect(db_path) as conn:
        if status:
            rows = conn.execute(
                """
                SELECT * FROM pending_trades
                WHERE status=?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (status, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM pending_trades
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]
