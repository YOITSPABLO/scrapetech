import argparse
import asyncio
import os
from .logging_setup import setup_logging
from .telethon_listener import run_listen
from .db import init_db, smoke, connect

def main():
    parser = argparse.ArgumentParser("scrapetech")
    sub = parser.add_subparsers(dest="command", required=False)

    p_listen = sub.add_parser("listen", help="Listen to a Telegram channel")
    p_listen.add_argument("--channel")
    p_listen.add_argument("--log-level", default="INFO")

    p_db = sub.add_parser("db", help="Database utilities")
    db_sub = p_db.add_subparsers(dest="dbcmd", required=True)
    db_sub.add_parser("init")
    db_sub.add_parser("smoke")
    tail = db_sub.add_parser("tail", help="Show last N signals")
    tail.add_argument("-n", type=int, default=10)

    args = parser.parse_args()

    if not args.command:
        print("Scrapetech booted")
        return

    setup_logging(getattr(args, "log_level", "INFO"))

    if args.command == "listen":
        channel = args.channel or os.getenv("TEST_CHANNEL")
        if not channel:
            raise SystemExit("Provide --channel or set TEST_CHANNEL")
        asyncio.run(run_listen(channel))
        return

    if args.command == "db":
        if args.dbcmd == "init":
            init_db()
            print("DB INIT OK")
            return
        if args.dbcmd == "smoke":
            smoke()
            return
        if args.dbcmd == "tail":
            init_db()
            with connect() as conn:
                rows = conn.execute("""
                    SELECT s.id, c.handle, s.mint, s.confidence, s.created_at
                    FROM signals s
                    JOIN channels c ON c.id = s.channel_id
                    ORDER BY s.id DESC
                    LIMIT ?
                """, (args.n,)).fetchall()
            for r in rows[::-1]:
                print(f"{r['id']} | {r['created_at']} | {r['handle']} | {r['mint']} | conf={r['confidence']}")
            return
