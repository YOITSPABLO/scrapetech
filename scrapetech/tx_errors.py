import re


def format_tx_error(err: object) -> str:
    msg = str(err) if err is not None else ""
    if not msg:
        return "Transaction failed."

    # Try to extract the inner RPC message for readability.
    inner = re.search(r"'message': '([^']+)'", msg)
    if inner:
        msg = inner.group(1)

    lower = msg.lower()

    if "bondingcurvecomplete" in msg or "custom program error: 0x1775" in msg:
        return "Bonding curve complete (migrated to Raydium)."
    if "accountnotfound" in lower:
        return "Wallet has no SOL (account not funded)."
    if "insufficient lamports" in lower:
        m = re.search(r"insufficient lamports (\d+), need (\d+)", msg)
        if m:
            have = int(m.group(1)) / 1e9
            need = int(m.group(2)) / 1e9
            return f"Insufficient SOL for fees (need {need:.6f}, have {have:.6f})."
        return "Insufficient SOL for fees."
    if "transaction processed but receipt not available" in lower:
        return "Transaction submitted; awaiting confirmation."
    if "transaction simulation failed" in lower:
        return "Transaction simulation failed."
    if "custom program error: 0x1" in lower or "custom': 1" in lower:
        return "Transaction failed (program error)."

    return msg
