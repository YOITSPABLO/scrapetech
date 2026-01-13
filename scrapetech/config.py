from dataclasses import dataclass
import os
from dotenv import load_dotenv
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CANDIDATES = [_HERE / ".env", _HERE.parent / ".env"]
for _p in _CANDIDATES:
    if _p.exists():
        load_dotenv(dotenv_path=_p)
        break
else:
    load_dotenv()

@dataclass(frozen=True)
class Settings:
    telegram_api_id: int
    telegram_api_hash: str
    telethon_session: str

    @staticmethod
    def from_env():
        api_id = os.getenv("TELEGRAM_API_ID", "").strip()
        api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
        session = os.getenv("TELETHON_SESSION", "scrapetech_session").strip()

        if not api_id.isdigit():
            raise ValueError("TELEGRAM_API_ID missing or invalid")
        if not api_hash:
            raise ValueError("TELEGRAM_API_HASH missing")

        return Settings(
            telegram_api_id=int(api_id),
            telegram_api_hash=api_hash,
            telethon_session=session,
        )
