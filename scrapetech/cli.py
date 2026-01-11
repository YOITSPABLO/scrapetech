import argparse
import asyncio
import os
from .logging_setup import setup_logging
from .telethon_listener import run_listen
from .db import (
    init_db, smoke, connect,
    upsert_subscription, list_subscriptions,
    update_user_settings, get_user_settings,
    tail_trade_intents,
)
from .wallets import wallet_create, wallet_import, wallet_get_pubkey

def main():
    parser = argparse.ArgumentParser("scrapetech")
    sub = parser.add_subparsers(dest="command", required=False)

    # listen
    p_listen = sub.add_parser("listen", help="Listen to a Telegram channel")
    p_listen.add_argument("--channel")
    p_listen.add_argument("--log-level", default="INFO")

    # db
    p_db = sub.add_parser("db", help="Database utilities")
    db_sub = p_db.add_subparsers(dest="dbcmd", required=True)
    db_sub.add_parser("init")
    db_sub.add_parser("smoke")
    db_sub.add_parser("schema")
    tail = db_sub.add_parser("tail", help="Show last N signals")
    tail.add_argument("-n", type=int, default=10)

    # subscriptions
    p_sub = sub.add_parser("sub", help="Subscription utilities (CLI testing)")
    sub_sub = p_sub.add_subparsers(dest="subcmd", required=True)
    sset = sub_sub.add_parser("set", help="Set subscription status for a user+channel")
    sset.add_argument("--user", required=True)
    sset.add_argument("--channel", required=True)
    sset.add_argument("--status", required=True, choices=["ACTIVE", "PAUSED", "STOPPED", "DELETED"])
    sls = sub_sub.add_parser("list", help="List subscriptions for a user")
    sls.add_argument("--user", required=True)

    # settings
    p_set = sub.add_parser("settings", help="User settings (CLI testing)")
    set_sub = p_set.add_subparsers(dest="setcmd", required=True)
    set_show = set_sub.add_parser("show")
    set_show.add_argument("--user", required=True)
    set_update = set_sub.add_parser("set")
    set_update.add_argument("--user", required=True)
    set_update.add_argument("--trade-mode", choices=["normal", "degen"])
    set_update.add_argument("--position-mode", choices=["single", "multi"])
    set_update.add_argument("--max-open-positions", type=int)
    set_update.add_argument("--buy-amount-sol", type=float)
    set_update.add_argument("--buy-slippage-pct", type=float)
    set_update.add_argument("--sell-slippage-pct", type=float)
    set_update.add_argument("--tp-sl-enabled", choices=["0", "1"])
    set_update.add_argument("--take-profit-pct", type=float)
    set_update.add_argument("--stop-loss-pct", type=float)
    set_update.add_argument("--cooldown-seconds", type=int)
    set_update.add_argument("--max-trades-per-day", type=int)
    set_update.add_argument("--duplicate-mint-block", choices=["0", "1"])

    # intents
    p_int = sub.add_parser("intents", help="Trade intents (CLI testing)")
    int_sub = p_int.add_subparsers(dest="intcmd", required=True)
    int_tail = int_sub.add_parser("tail")
    int_tail.add_argument("-n", type=int, default=20)

    # wallets
    p_w = sub.add_parser("wallet", help="Wallet utilities (CLI testing)")
    w_sub = p_w.add_subparsers(dest="wcmd", required=True)
    w_create = w_sub.add_parser("create")
    w_create.add_argument("--user", required=True)
    w_import = w_sub.add_parser("import")
    w_import.add_argument("--user", required=True)
    w_import.add_argument("--secret", required=True, help="base58 seed/secret or JSON array")
    w_show = w_sub.add_parser("show")
    w_show.add_argument("--user", required=True)

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
        if args.dbcmd == "schema":
            init_db()
            with connect() as conn:
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                for t in tables:
                    name = t["name"]
                    print(f"\n{name}:")
                    cols = conn.execute(f"PRAGMA table_info({name})").fetchall()
                    for c in cols:
                        pk = " PRIMARY KEY" if c["pk"] else ""
                        print(f"  - {c['name']} {c['type']}{pk}")
            return
        if args.dbcmd == "tail":
            init_db()
            with connect() as conn:
                rows = conn.execute(
                    """
                    SELECT s.id, c.handle, s.mint, s.confidence, s.created_at
                    FROM signals s
                    JOIN channels c ON c.id = s.channel_id
                    ORDER BY s.id DESC
                    LIMIT ?
                    """,
                    (args.n,),
                ).fetchall()
            for r in rows[::-1]:
                print(f"{r['id']} | {r['created_at']} | {r['handle']} | {r['mint']} | conf={r['confidence']}")
            return

    if args.command == "sub":
        if args.subcmd == "set":
            upsert_subscription(args.user, args.channel, args.status)
            print(f"SUB OK: user={args.user} channel={args.channel} status={args.status}")
            return
        if args.subcmd == "list":
            rows = list_subscriptions(args.user)
            if not rows:
                print("No subscriptions found.")
                return
            for r in rows:
                print(f"{r['handle']} | {r['status']} | {r['created_at']}")
            return

    if args.command == "settings":
        if args.setcmd == "show":
            s = get_user_settings(args.user)
            for k in sorted(s.keys()):
                if k in ("id", "user_id"):
                    continue
                print(f"{k}={s[k]}")
            return

        if args.setcmd == "set":
            updates = {}
            if args.trade_mode is not None:
                updates["trade_mode"] = args.trade_mode
            if args.position_mode is not None:
                updates["position_mode"] = args.position_mode
            if args.max_open_positions is not None:
                updates["max_open_positions"] = args.max_open_positions
            if args.buy_amount_sol is not None:
                updates["buy_amount_sol"] = args.buy_amount_sol
            if args.buy_slippage_pct is not None:
                updates["buy_slippage_pct"] = args.buy_slippage_pct
            if args.sell_slippage_pct is not None:
                updates["sell_slippage_pct"] = args.sell_slippage_pct
            if args.tp_sl_enabled is not None:
                updates["tp_sl_enabled"] = int(args.tp_sl_enabled)
            if args.take_profit_pct is not None:
                updates["take_profit_pct"] = args.take_profit_pct
            if args.stop_loss_pct is not None:
                updates["stop_loss_pct"] = args.stop_loss_pct
            if args.cooldown_seconds is not None:
                updates["cooldown_seconds"] = args.cooldown_seconds
            if args.max_trades_per_day is not None:
                updates["max_trades_per_day"] = args.max_trades_per_day
            if args.duplicate_mint_block is not None:
                updates["duplicate_mint_block"] = int(args.duplicate_mint_block)

            if not updates:
                print("No updates provided.")
                return

            update_user_settings(args.user, updates)
            print("SETTINGS OK")
            return

    if args.command == "intents":
        if args.intcmd == "tail":
            rows = tail_trade_intents(args.n)
            if not rows:
                print("No intents found.")
                return
            for r in rows[::-1]:
                print(
                    f"{r['id']} | {r['created_at']} | user={r['telegram_user_id']} | {r['handle']} | "
                    f"{r['intent_type']} | {r['mint']} | {r['status']} | {r['reason'] or ''}"
                )
            return

    if args.command == "wallet":
        if args.wcmd == "create":
            out = wallet_create(args.user)
            print(f"WALLET OK: user={args.user} pubkey={out['pubkey']}")
            print("\nBACKUP OPTIONS (SAVE ONE OF THESE):")
            print("1) Phantom secret key (base58, 64 bytes) — Phantom-friendly:")
            print(out['phantom_secret_base58'])
            print("\n2) Phantom secret key (JSON array, 64 ints) — also Phantom-friendly:")
            print(out['phantom_secret_json'])
            print("\n3) Seed (base58, 32 bytes) — dev format:")
            print(out['seed_base58'])
            return
        if args.wcmd == "import":
            rec = wallet_import(args.user, args.secret)
            print(f"WALLET OK: user={args.user} pubkey={rec.pubkey}")
            return
        if args.wcmd == "show":
            pub = wallet_get_pubkey(args.user)
            if not pub:
                print("No wallet found.")
                return
            print(f"user={args.user} pubkey={pub}")
            return
