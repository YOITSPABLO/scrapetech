import argparse
import asyncio
import os
from .logging_setup import setup_logging
from .telethon_listener import run_listen

def main():
    parser = argparse.ArgumentParser("scrapetech")
    parser.add_argument("command", choices=["listen"])
    parser.add_argument("--channel")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    setup_logging(args.log_level)

    if args.command == "listen":
        channel = args.channel or os.getenv("TEST_CHANNEL")
        if not channel:
            raise SystemExit("Provide --channel or set TEST_CHANNEL")
        asyncio.run(run_listen(channel))
