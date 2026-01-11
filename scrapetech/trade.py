import re
from dataclasses import dataclass

from .wallets import wallet_get_pubkey
from .db import get_user_settings

BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

@dataclass(frozen=True)
class TradePlan:
    telegram_user_id: str
    pubkey: str
    mint: str
    buy_amount_sol: float
    buy_slippage_pct: float
    trade_mode: str
    position_mode: str
    max_open_positions: int

def build_buy_plan(telegram_user_id: str, mint: str) -> TradePlan:
    mint = mint.strip()
    if not BASE58_RE.match(mint):
        raise ValueError("Mint/CA must be base58 32-44 chars (no spaces/brackets)")

    pubkey = wallet_get_pubkey(telegram_user_id)
    if not pubkey:
        raise ValueError(f"No wallet for user {telegram_user_id}. Run: scrapetech wallet create/import")

    s = get_user_settings(telegram_user_id)

    return TradePlan(
        telegram_user_id=telegram_user_id,
        pubkey=pubkey,
        mint=mint,
        buy_amount_sol=float(s.get("buy_amount_sol", 0.01)),
        buy_slippage_pct=float(s.get("buy_slippage_pct", 20.0)),
        trade_mode=str(s.get("trade_mode", "normal")),
        position_mode=str(s.get("position_mode", "single")),
        max_open_positions=int(s.get("max_open_positions", 1)),
    )

def print_buy_plan(plan: TradePlan, dry_run: bool = True) -> None:
    print("=== Scrapetech Manual Buy (Plan) ===")
    print(f"USER: {plan.telegram_user_id}")
    print(f"WALLET: {plan.pubkey}")
    print(f"MINT: {plan.mint}")
    print(f"MODE: {plan.trade_mode}")
    print(f"POSITION MODE: {plan.position_mode} (max_open_positions={plan.max_open_positions})")
    print(f"BUY AMOUNT: {plan.buy_amount_sol} SOL")
    print(f"BUY SLIPPAGE: {plan.buy_slippage_pct}%")
    print("")
    print("NOTE: Execution engine not implemented yet.")
    print("DRY RUN:", "yes" if dry_run else "no")
