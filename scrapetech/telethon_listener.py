import logging
from telethon import TelegramClient, events
from .config import Settings
from .detector import detect_mints
from .db import get_or_create_channel, insert_message, insert_signal

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

    channel_id = get_or_create_channel(channel)

    @client.on(events.NewMessage(chats=entity))
    async def handler(event):
        text = (event.raw_text or "").strip()
        if not text:
            return

        clean = text.replace("\n", " ")
        log.info("MSG %s | %s", event.id, clean[:120])

        message_id = insert_message(channel_id=channel_id, telegram_message_id=int(event.id), text=text)

        for dm in detect_mints(text):
            log.info("DETECTED mint=%s confidence=%s", dm.mint, dm.confidence)
            insert_signal(channel_id=channel_id, message_id=message_id, mint=dm.mint, confidence=int(dm.confidence))

    log.info("Listening on %s", channel)
    await client.run_until_disconnected()
