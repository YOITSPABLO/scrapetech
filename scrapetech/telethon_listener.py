import logging
from telethon import TelegramClient, events
from .config import Settings
from .detector import detect_mints

log = logging.getLogger("scrapetech")

async def run_listen(channel: str) -> None:
    settings = Settings.from_env()

    client = TelegramClient(
        settings.telethon_session,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

    await client.start()

    try:
        entity = await client.get_input_entity(channel)
    except Exception:
        log.error("Could not resolve channel '%s'. Check the @username and that your account can access it.", channel)
        raise

    @client.on(events.NewMessage(chats=entity))
    async def handler(event):
        text = (event.raw_text or "").replace("\n", " ")
        log.info("MSG %s | %s", event.id, text[:120])

        for dm in detect_mints(text):
            log.info("DETECTED mint=%s confidence=%s", dm.mint, dm.confidence)

    log.info("Listening on %s", channel)
    await client.run_until_disconnected()
