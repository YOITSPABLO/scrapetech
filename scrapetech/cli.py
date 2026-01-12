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
    apply_trade, get_position, list_positions,
    enqueue_pending_trade, update_pending_trade_status, list_pending_trades, get_telegram_user_id,
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
import time

from .pump_sell import build_sell_ix_and_plan, build_and_simulate_sell_tx, send_sell_tx
from .solana_rpc import try_get_mint_decimals, rpc_get_transaction, extract_tx_deltas, get_http_client
from .auto_trader import monitor_positions_loop
from .bot import run_bot


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

    # reconcile
    p_rec = sub.add_parser("reconcile", help="Reconcile pending trades")
    p_rec.add_argument("--limit", type=int, default=20)
    p_rec.add_argument("--status", default="PENDING")
    p_rec.add_argument("--watch", action="store_true", help="Loop and reconcile periodically")
    p_rec.add_argument("--interval", type=int, default=10, help="Seconds between reconcile runs")

    # monitor
    p_mon = sub.add_parser("monitor", help="Monitor positions for TP/SL and auto-sell")
    p_mon.add_argument("--interval", type=int, default=10)

    # bot
    sub.add_parser("bot", help="Run Telegram bot commands (user-facing)")

    # positions
    p_pos = sub.add_parser("pos", help="Position utilities (CLI testing)")
    pos_sub = p_pos.add_subparsers(dest="poscmd", required=True)
    pos_show = pos_sub.add_parser("show")
    pos_show.add_argument("--user", required=True)
    pos_show.add_argument("--mint")
    pos_apply = pos_sub.add_parser("apply")
    pos_apply.add_argument("--user", required=True)
    pos_apply.add_argument("--mint", required=True)
    pos_apply.add_argument("--side", required=True, choices=["BUY", "SELL"])
    pos_apply.add_argument("--tokens", required=True, type=float)
    pos_apply.add_argument("--sol", required=True, type=float)
    pos_apply.add_argument("--tx", default=None)

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

    # sell (pumpfun)
    e_build_sell = e_sub.add_parser("build-sell", help="Build Pump.fun sell instruction (no tx)")
    e_build_sell.add_argument("--user", required=True)
    e_build_sell.add_argument("--mint", required=True)
    e_build_sell.add_argument("--pct", type=float, default=None)
    e_build_sell.add_argument("--tokens", type=float, default=None)
    e_build_sell.add_argument("--min-sol", type=float, default=None)
    e_build_sell.add_argument("--min-sol-lamports", type=int, default=None)

    e_sim_selltx = e_sub.add_parser("simulate-selltx", help="Simulate Pump.fun sell tx (no send)")
    e_sim_selltx.add_argument("--user", required=True)
    e_sim_selltx.add_argument("--mint", required=True)
    e_sim_selltx.add_argument("--pct", type=float, default=None)
    e_sim_selltx.add_argument("--tokens", type=float, default=None)
    e_sim_selltx.add_argument("--min-sol", type=float, default=None)
    e_sim_selltx.add_argument("--min-sol-lamports", type=int, default=None)

    e_send_sell = e_sub.add_parser("send-sell", help="SEND Pump.fun sell tx (REAL TX)")
    e_send_sell.add_argument("--user", required=True)
    e_send_sell.add_argument("--mint", required=True)
    e_send_sell.add_argument("--pct", type=float, default=None)
    e_send_sell.add_argument("--tokens", type=float, default=None)
    e_send_sell.add_argument("--min-sol", type=float, default=None)
    e_send_sell.add_argument("--min-sol-lamports", type=int, default=None)

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

    if args.command == "reconcile":
        status = args.status.strip().upper() if args.status else None
        if status in ("ALL", "*"):
            status = None

        def _run_once():
            rows = list_pending_trades(status=status, limit=args.limit)
            if not rows:
                print("No pending trades.")
                return

            http = get_http_client()
            for r in rows:
                sig = r["signature"]
                tx = rpc_get_transaction(http, sig)
                if not tx or not tx.get("meta"):
                    continue

                meta = tx.get("meta") or {}
                if meta.get("err"):
                    update_pending_trade_status(sig, "FAILED", error=str(meta.get("err")))
                    print(f"FAILED {sig} err={meta.get('err')}")
                    continue

                telegram_user_id = get_telegram_user_id(r["user_id"])
                if not telegram_user_id:
                    update_pending_trade_status(sig, "FAILED", error="user not found")
                    print(f"FAILED {sig} err=user not found")
                    continue

                owner_pubkey = wallet_get_pubkey(telegram_user_id)
                if not owner_pubkey:
                    update_pending_trade_status(sig, "FAILED", error="wallet not found")
                    print(f"FAILED {sig} err=wallet not found")
                    continue

                deltas = extract_tx_deltas(tx, owner_pubkey=owner_pubkey, mint=r["mint"])
                sol_delta = deltas.get("sol_delta_lamports")
                token_delta_ui = deltas.get("token_delta_ui")
                if sol_delta is None or token_delta_ui is None:
                    continue

                actual_sol = abs(sol_delta) / 1_000_000_000
                actual_tokens = abs(token_delta_ui)
                apply_trade(
                    telegram_user_id,
                    r["mint"],
                    r["side"],
                    actual_tokens,
                    actual_sol,
                    tx_sig=sig,
                )
                update_pending_trade_status(
                    sig,
                    "SUCCESS",
                    actual_token_amount=actual_tokens,
                    actual_sol_amount=actual_sol,
                )
                print(f"OK {sig} tokens={actual_tokens} sol={actual_sol}")

        if not args.watch:
            _run_once()
            return

        while True:
            _run_once()
            time.sleep(max(1, int(args.interval)))

    if args.command == "monitor":
        print(f"Monitor started (interval={args.interval}s)")
        monitor_positions_loop(interval=args.interval)

    if args.command == "bot":
        asyncio.run(run_bot())

    if args.command == "pos":
        if args.poscmd == "show":
            if args.mint:
                row = get_position(args.user, args.mint)
                if not row:
                    print("No position found.")
                    return
                print(
                    f"{row['mint']} | tokens={row['token_balance']} | avg_entry={row['avg_entry_sol']} | "
                    f"spent={row['total_sol_spent']} | received={row['total_sol_received']} | "
                    f"realized_pnl={row['realized_pnl_sol']} | open={row['open']}"
                )
                return
            rows = list_positions(args.user)
            if not rows:
                print("No positions found.")
                return
            for row in rows:
                print(
                    f"{row['mint']} | tokens={row['token_balance']} | avg_entry={row['avg_entry_sol']} | "
                    f"spent={row['total_sol_spent']} | received={row['total_sol_received']} | "
                    f"realized_pnl={row['realized_pnl_sol']} | open={row['open']}"
                )
            return
        if args.poscmd == "apply":
            row = apply_trade(
                args.user,
                args.mint,
                args.side,
                args.tokens,
                args.sol,
                tx_sig=args.tx,
            )
            if not row:
                print("No position found.")
                return
            print(
                f"{row['mint']} | tokens={row['token_balance']} | avg_entry={row['avg_entry_sol']} | "
                f"spent={row['total_sol_spent']} | received={row['total_sol_received']} | "
                f"realized_pnl={row['realized_pnl_sol']} | open={row['open']}"
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
        def _resolve_sell_amount(user: str, mint: str, pct: float | None, tokens_ui: float | None):
            if tokens_ui is None:
                pct_val = 100.0 if pct is None else float(pct)
                if pct_val <= 0 or pct_val > 100:
                    raise ValueError("pct must be in (0,100]")
                pos = get_position(user, mint)
                if not pos or float(pos.get("token_balance") or 0) <= 0:
                    raise ValueError("No position balance found for this user+mint")
                tokens_ui = float(pos["token_balance"]) * (pct_val / 100.0)

            decimals = try_get_mint_decimals(mint)
            if decimals is None:
                raise ValueError("Could not determine mint decimals for sell sizing")

            tokens_raw = int(tokens_ui * (10 ** int(decimals)))
            if tokens_raw <= 0:
                raise ValueError("tokens_to_sell_raw computed as 0; adjust pct/tokens")
            return tokens_ui, tokens_raw, int(decimals)

        def _resolve_min_sol(min_sol: float | None, min_sol_lamports: int | None) -> int:
            if min_sol_lamports is not None:
                return int(min_sol_lamports)
            if min_sol is not None:
                return int(float(min_sol) * 1_000_000_000)
            return 1

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
            print(f"ASSOCIATED USER: {plan.user_ata}")
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

            req_tokens_ui = None
            decimals = try_get_mint_decimals(plan.mint)
            if decimals is not None:
                req_tokens_ui = plan.tokens_out_raw / (10 ** int(decimals))

            enqueue_pending_trade(
                args.user,
                plan.mint,
                "BUY",
                sig,
                requested_token_amount=req_tokens_ui,
                requested_sol_amount=sol_in,
            )

            sol_amount = None
            token_amount_ui = None
            http = get_http_client()
            for _ in range(6):
                try:
                    tx = rpc_get_transaction(http, sig)
                    deltas = extract_tx_deltas(tx, plan.user_pubkey, plan.mint)
                    sol_delta = deltas.get("sol_delta_lamports")
                    token_delta_ui = deltas.get("token_delta_ui")
                    if sol_delta is not None:
                        sol_amount = abs(sol_delta) / 1_000_000_000
                    if token_delta_ui is not None:
                        token_amount_ui = abs(token_delta_ui)
                    if sol_amount is not None and token_amount_ui is not None:
                        break
                except Exception:
                    pass
                time.sleep(2)

            if sol_amount is not None and token_amount_ui is not None:
                try:
                    apply_trade(
                        args.user,
                        plan.mint,
                        "BUY",
                        float(token_amount_ui),
                        float(sol_amount),
                        tx_sig=sig,
                    )
                    update_pending_trade_status(
                        sig,
                        "SUCCESS",
                        actual_token_amount=float(token_amount_ui),
                        actual_sol_amount=float(sol_amount),
                    )
                except Exception:
                    pass

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
            from .pump_tx import ata_create_idempotent_ix
            from .solana_rpc import rpc_get_multiple_accounts
        
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

        # build-sell
        if args.ecmd == "build-sell":
            tokens_ui, tokens_raw, decimals = _resolve_sell_amount(
                args.user, args.mint, args.pct, args.tokens
            )
            min_sol = _resolve_min_sol(args.min_sol, args.min_sol_lamports)

            kp = load_keypair_for_user(args.user)
            plan, _ix = build_sell_ix_and_plan(
                user_keypair=kp,
                mint_str=args.mint,
                tokens_to_sell_raw=tokens_raw,
                min_sol_output_lamports=min_sol,
            )

            print("=== Scrapetech Build Sell (Pump.fun) ===")
            print(f"USER: {args.user}")
            print(f"WALLET: {plan.user_pubkey}")
            print(f"MINT: {plan.mint}")
            print(f"TOKEN PROGRAM: {plan.token_program}")
            print(f"BONDING CURVE: {plan.bonding_curve}")
            print(f"ASSOCIATED USER: {plan.user_ata}")
            print(f"TOKENS TO SELL (raw): {plan.tokens_to_sell_raw}")
            print(f"TOKENS TO SELL (ui): {tokens_ui} (decimals={decimals})")
            print(f"MIN SOL OUTPUT (lamports): {plan.min_sol_output_lamports}")
            print("NO TRANSACTION SENT.")
            return

        # simulate-selltx
        if args.ecmd == "simulate-selltx":
            tokens_ui, tokens_raw, decimals = _resolve_sell_amount(
                args.user, args.mint, args.pct, args.tokens
            )
            min_sol = _resolve_min_sol(args.min_sol, args.min_sol_lamports)

            kp = load_keypair_for_user(args.user)
            plan, sell_ix = build_sell_ix_and_plan(
                user_keypair=kp,
                mint_str=args.mint,
                tokens_to_sell_raw=tokens_raw,
                min_sol_output_lamports=min_sol,
            )
            sim = build_and_simulate_sell_tx(user_keypair=kp, sell_ix=sell_ix)

            print("=== Scrapetech Simulate Sell TX (Pump.fun) ===")
            print(f"USER: {args.user}")
            print(f"WALLET: {plan.user_pubkey}")
            print(f"MINT: {plan.mint}")
            print(f"TOKENS TO SELL (raw): {plan.tokens_to_sell_raw}")
            print(f"TOKENS TO SELL (ui): {tokens_ui} (decimals={decimals})")
            print(f"MIN SOL OUTPUT (lamports): {plan.min_sol_output_lamports}")
            print(f"SIM ERR: {sim.get('err')}")
            logs = sim.get("logs")
            if logs:
                print("---- LOGS ----")
                for line in logs:
                    print(line)
            print("NO TRANSACTION SENT.")
            return

        # send-sell (REAL SEND)
        if args.ecmd == "send-sell":
            tokens_ui, tokens_raw, decimals = _resolve_sell_amount(
                args.user, args.mint, args.pct, args.tokens
            )
            min_sol = _resolve_min_sol(args.min_sol, args.min_sol_lamports)

            kp = load_keypair_for_user(args.user)
            plan, sell_ix = build_sell_ix_and_plan(
                user_keypair=kp,
                mint_str=args.mint,
                tokens_to_sell_raw=tokens_raw,
                min_sol_output_lamports=min_sol,
            )
            sig = send_sell_tx(user_keypair=kp, sell_ix=sell_ix)

            enqueue_pending_trade(
                args.user,
                plan.mint,
                "SELL",
                sig,
                requested_token_amount=float(tokens_ui),
                requested_sol_amount=float(min_sol) / 1_000_000_000,
            )

            sol_amount = None
            token_amount_ui = None
            http = get_http_client()
            for _ in range(6):
                try:
                    tx = rpc_get_transaction(http, sig)
                    deltas = extract_tx_deltas(tx, plan.user_pubkey, plan.mint)
                    sol_delta = deltas.get("sol_delta_lamports")
                    token_delta_ui = deltas.get("token_delta_ui")
                    if sol_delta is not None:
                        sol_amount = sol_delta / 1_000_000_000
                    if token_delta_ui is not None:
                        token_amount_ui = abs(token_delta_ui)
                    if sol_amount is not None and token_amount_ui is not None:
                        break
                except Exception:
                    pass
                time.sleep(2)

            if sol_amount is not None and token_amount_ui is not None:
                try:
                    apply_trade(
                        args.user,
                        args.mint,
                        "SELL",
                        float(token_amount_ui),
                        float(sol_amount),
                        tx_sig=sig,
                    )
                    update_pending_trade_status(
                        sig,
                        "SUCCESS",
                        actual_token_amount=float(token_amount_ui),
                        actual_sol_amount=float(sol_amount),
                    )
                except Exception:
                    pass

            print("=== Scrapetech SEND SELL (Pump.fun) ===")
            print(f"USER: {args.user}")
            print(f"WALLET: {plan.user_pubkey}")
            print(f"MINT: {plan.mint}")
            print(f"TOKENS TO SELL (raw): {plan.tokens_to_sell_raw}")
            print(f"TOKENS TO SELL (ui): {tokens_ui} (decimals={decimals})")
            print(f"MIN SOL OUTPUT (lamports): {plan.min_sol_output_lamports}")
            print(f"SIGNATURE: {sig}")
            if sig:
                print(f"SOLSCAN: https://solscan.io/tx/{sig}")
            return
if __name__ == "__main__":
    main()
