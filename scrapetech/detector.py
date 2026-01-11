import re
from dataclasses import dataclass
from typing import List

_BASE58 = r"[1-9A-HJ-NP-Za-km-z]"
MINT_RE = re.compile(rf"(?<!{_BASE58})({_BASE58}{{32,44}})(?!{_BASE58})")

@dataclass(frozen=True)
class DetectedMint:
    mint: str
    confidence: int

def detect_mints(text: str) -> List[DetectedMint]:
    if not text:
        return []

    hits: List[DetectedMint] = []
    for m in MINT_RE.finditer(text):
        mint = m.group(1)

        window = text[max(0, m.start()-40):min(len(text), m.end()+40)].lower()
        score = 50
        if "ca" in window or "contract" in window or "mint" in window or "address" in window:
            score += 25
        if "pump" in window or "pump.fun" in window or "bonding" in window:
            score += 10
        score = max(0, min(100, score))

        hits.append(DetectedMint(mint=mint, confidence=score))

    # de-dupe (preserve order)
    seen = set()
    out = []
    for h in hits:
        if h.mint not in seen:
            out.append(h)
            seen.add(h.mint)
    return out
