from solders.pubkey import Pubkey
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
from .trade import build_buy_plan, print_buy_plan

# Pump.fun quoting + tx building/sim
from .pump_quotes import quote_buy_pumpfun
from .pump_tx import (
    load_keypair_for_user,
    build_buy_ix_and_plan,
    build_and_simulate_buy_tx,
    send_buy_tx,
    dump_ix_accounts,
)


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

    # trade (dry-run plan)
    p_t = sub.add_parser("trade", help="Manual trading (dry-run plan)")
    t_sub = p_t.add_subparsers(dest="tcmd", required=True)
    t_buy = t_sub.add_parser("buy", help="Build a buy plan (dry-run)")
    t_buy.add_argument("--user", required=True)
    t_buy.add_argument("--mint", required=True)

    # exec (pumpfun)
    p_e = sub.add_parser("exec", help="Pump.fun execution (quote/build/sim/dump)")
    e_sub = p_e.add_subparsers(dest="ecmd", required=True)

    e_quote_buy = e_sub.add_parser("quote-buy", help="Quote a buy (no tx sent)")
    e_quote_buy.add_argument("--user", required=True)
    e_quote_buy.add_argument("--mint", required=True)
    e_quote_buy.add_argument("--sol", type=float, default=None)

    e_build_buy = e_sub.add_parser("build-buy", help="Build Pump.fun buy instruction (no tx)")
    e_build_buy.add_argument("--user", required=True)
    e_build_buy.add_argument("--mint", required=True)
    e_build_buy.add_argument("--sol", type=float, default=None)
    e_build_buy.add_argument("--slippage", type=float, default=None)

    e_sim_buytx = e_sub.add_parser("simulate-buytx", help="Simulate Pump.fun buy tx (no send)")

    e_send_buy = e_sub.add_parser("send-buy", help="SEND Pump.fun buy tx (REAL TX)")
    e_send_buy.add_argument("--user", required=True)
    e_send_buy.add_argument("--mint", required=True)
    e_send_buy.add_argument("--sol", type=float, default=None)
    e_send_buy.add_argument("--slippage", type=float, default=None)
    e_sim_buytx.add_argument("--user", required=True)
    e_sim_buytx.add_argument("--mint", required=True)
    e_sim_buytx.add_argument("--sol", type=float, default=None)
    e_sim_buytx.add_argument("--slippage", type=float, default=None)

    e_dump_buytx = e_sub.add_parser("dump-buytx", help="Dump tx account order + existence")
    e_dump_buytx.add_argument("--user", required=True)
    e_dump_buytx.add_argument("--mint", required=True)
    e_dump_buytx.add_argument("--sol", type=float, default=None)
    e_dump_buytx.add_argument("--slippage", type=float, default=None)

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
            print(out["phantom_secret_base58"])
            print("\n2) Phantom secret key (JSON array, 64 ints) — also Phantom-friendly:")
            print(out["phantom_secret_json"])
            print("\n3) Seed (base58, 32 bytes) — dev format:")
            print(out["seed_base58"])
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

    if args.command == "trade":
        if args.tcmd == "buy":
            plan = build_buy_plan(args.user, args.mint)
            print_buy_plan(plan, dry_run=True)
            return

    if args.command == "exec":
        # quote-buy
        if args.ecmd == "quote-buy":
            from .db import get_or_create_user
            get_or_create_user(args.user)
            s = get_user_settings(args.user)
            sol_in = float(args.sol) if args.sol is not None else float(s["buy_amount_sol"])
            q = quote_buy_pumpfun(args.mint, sol_in=sol_in, fee_bps=0)

            print("=== Scrapetech Quote Buy ===")
            print(f"USER: {args.user}")
            print(f"MINT: {q.mint}")
            print(f"ROUTE: {q.route}")
            print(f"SOL IN: {q.sol_in}")
            print(f"EST TOKENS OUT (raw units): {q.est_tokens_out_raw}")
            if getattr(q, "mint_decimals", None) is not None and getattr(q, "est_tokens_out_ui", None) is not None:
                print(f"EST TOKENS OUT (UI): {q.est_tokens_out_ui:,.6f} (decimals={q.mint_decimals})")
            if getattr(q, "est_price_sol_per_token", None) is not None and getattr(q, "est_tokens_out_ui", None):
                if q.est_tokens_out_ui and q.est_tokens_out_ui > 0:
                    sol_per_token = q.sol_in / q.est_tokens_out_ui
                    tokens_per_sol = q.est_tokens_out_ui / q.sol_in
                    print(f"PRICE: {sol_per_token:.12f} SOL per token")
                    print(f"PRICE: {tokens_per_sol:,.2f} tokens per SOL")
            if getattr(q, "token_program", None) is not None:
                print(f"TOKEN PROGRAM: {q.token_program}")
            print(f"CURVE PDA: {q.curve_pda}")
            print(f"CREATOR: {q.creator}")
            print(f"CURVE COMPLETE: {q.curve_complete}")
            print("NO TRANSACTION SENT.")
            return

        # build-buy
        if args.ecmd == "build-buy":
            from .db import get_or_create_user
            get_or_create_user(args.user)
            settings = get_user_settings(args.user)
            sol_in = float(args.sol) if args.sol is not None else float(settings["buy_amount_sol"])
            sl = float(args.slippage) if args.slippage is not None else float(settings["buy_slippage_pct"])

            kp = load_keypair_for_user(args.user)
            plan, _ix = build_buy_ix_and_plan(user_keypair=kp, mint_str=args.mint, sol_in=sol_in, slippage_pct=sl)

            print("=== Scrapetech Build Buy (Pump.fun) ===")
            print(f"USER: {args.user}")
            print(f"WALLET: {plan.user_pubkey}")
            print(f"MINT: {plan.mint}")
            print(f"TOKEN PROGRAM: {plan.token_program}")
            print(f"BONDING CURVE: {plan.bonding_curve}")
            print(f"ASSOCIATED USER: {user_ata}")
            print(f"TOKENS OUT (raw): {plan.tokens_out_raw}")
            print(f"MAX SOL COST (lamports): {plan.max_sol_cost_lamports} (slippage={plan.slippage_pct}%)")
            print("NO TRANSACTION SENT.")
            return

        # simulate-buytx
        if args.ecmd == "simulate-buytx":
            from .db import get_or_create_user
            get_or_create_user(args.user)
            settings = get_user_settings(args.user)
            sol_in = float(args.sol) if args.sol is not None else float(settings["buy_amount_sol"])
            sl = float(args.slippage) if args.slippage is not None else float(settings["buy_slippage_pct"])

            kp = load_keypair_for_user(args.user)
            out = build_and_simulate_buy_tx(user_keypair=kp, mint_str=args.mint, sol_in=sol_in, slippage_pct=sl)

            plan = out["plan"]
            sim = out["simulate"]

            print("=== Scrapetech Simulate Buy TX (Pump.fun) ===")
            print(f"USER: {args.user}")
            print(f"WALLET: {plan.user_pubkey}")
            print(f"MINT: {plan.mint}")
            print(f"TOKEN PROGRAM: {plan.token_program}")
            print(f"TOKENS OUT (raw): {plan.tokens_out_raw}")
            print(f"MAX SOL COST (lamports): {plan.max_sol_cost_lamports} (slippage={plan.slippage_pct}%)")
            print(f"SIM ERR: {getattr(sim, 'err', None)}")
            logs = getattr(sim, "logs", None)
            if logs:
                print("---- LOGS ----")
                for line in logs:
                    print(line)
            print("NO TRANSACTION SENT.")
            return


        # send-buy (REAL SEND)
        if args.ecmd == "send-buy":
            from .db import get_or_create_user
            get_or_create_user(args.user)
            settings = get_user_settings(args.user)

            sol_in = float(args.sol) if args.sol is not None else float(settings["buy_amount_sol"])
            sl = float(args.slippage) if args.slippage is not None else float(settings["buy_slippage_pct"])

            kp = load_keypair_for_user(args.user)
            out = send_buy_tx(user_keypair=kp, mint_str=args.mint, sol_in=sol_in, slippage_pct=sl)

            plan = out["plan"]
            sig = out["sig"]

            print("=== Scrapetech SEND BUY (Pump.fun) ===")
            print(f"USER: {args.user}")
            print(f"WALLET: {plan.user_pubkey}")
            print(f"MINT: {plan.mint}")
            print(f"TOKEN PROGRAM: {plan.token_program}")
            print(f"TOKENS OUT (raw): {plan.tokens_out_raw}")
            print(f"MAX SOL COST (lamports): {plan.max_sol_cost_lamports} (slippage={plan.slippage_pct}%)")
            print(f"SIGNATURE: {sig}")
            if sig:
                print(f"SOLSCAN: https://solscan.io/tx/{sig}")
            return

        # dump-buytx
        if args.ecmd == "dump-buytx":
            from .db import get_or_create_user
            from .pump_tx import build_buy_ix_and_plan, ata_create_idempotent_ix, dump_ix_accounts
            from .solana_rpc import get_http_client, rpc_get_multiple_accounts
        
            get_or_create_user(args.user)
            settings = get_user_settings(args.user)
            sol_in = float(args.sol) if args.sol is not None else float(settings["buy_amount_sol"])
            sl = float(args.slippage) if args.slippage is not None else float(settings["buy_slippage_pct"])
        
            kp = load_keypair_for_user(args.user)
            plan, buy_ix = build_buy_ix_and_plan(user_keypair=kp, mint_str=args.mint, sol_in=sol_in, slippage_pct=sl)
        
            owner = kp.pubkey()
            mint = Pubkey.from_string(args.mint)
            token_program = Pubkey.from_string(str(plan.token_program))
            from spl.token.instructions import get_associated_token_address
            mint_pk = Pubkey.from_string(args.mint)
            owner = Pubkey.from_string(str(plan.user_pubkey))
            token_prog = Pubkey.from_string(str(plan.token_program))
            user_ata = get_associated_token_address(owner=owner, mint=mint_pk, token_program_id=token_prog)
            bonding_curve = Pubkey.from_string(str(plan.bonding_curve))
            assoc_bonding_curve = get_associated_token_address(owner=bonding_curve, mint=mint_pk, token_program_id=token_prog)
            ata_ix = ata_create_idempotent_ix(owner, owner, mint, user_ata, token_program)
        
            # collect all pubkeys referenced by both ixs (ATA + buy)
            all_pubkeys = []
            for ix in (ata_ix, buy_ix):
                for _i, _sig, _w, pk in dump_ix_accounts(ix):
                    if pk not in all_pubkeys:
                        all_pubkeys.append(pk)
        
            http = get_http_client()
            vals = rpc_get_multiple_accounts(http, all_pubkeys)
            missing = set(pk for pk, v in zip(all_pubkeys, vals) if v is None)
        
            print("=== Scrapetech Dump BuyTX ===")
            print(f"USER: {args.user}")
            print(f"WALLET: {plan.user_pubkey}")
            print(f"MINT: {plan.mint}")
            print(f"TOKEN PROGRAM: {plan.token_program}")
        
            if missing:
                print("\nMISSING (nonexistent) pubkeys:")
                for pk in sorted(missing):
                    print(f"  - {pk}")
        
            def _dump(ix, idx):
                print(f"\nIX {idx} program={ix.program_id}")
                for i, is_sig, is_w, pk in dump_ix_accounts(ix):
                    status = "MISSING" if pk in missing else "OK"
                    flags = ("W" if is_w else "-") + ("S" if is_sig else "-")
                    print(f"  [{i:02d}] {status} {flags} {pk}")
        
            _dump(ata_ix, 0)
            _dump(buy_ix, 1)
            return
if __name__ == "__main__":
    main()
