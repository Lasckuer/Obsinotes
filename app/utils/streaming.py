import time
from typing import AsyncGenerator
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

async def stream_to_message(
    processing_msg: Message,
    stream_generator: AsyncGenerator[str, None],
    status_prefix: str,
    update_interval: float = 2.0
) -> str:
    """
    Consumes an async stream of string tokens, updating the Telegram message
    periodically with animated frames and token/char counts to prevent flood limits.
    Returns the accumulated full string.
    """
    full_text = ""
    last_update_time = time.time()
    frames = ["🌘", "🌗", "🌖", "🌕", "🌔", "🌓", "🌒", "🌑"]
    frame_idx = 0

    async for chunk in stream_generator:
        full_text += chunk
        current_time = time.time()

        if current_time - last_update_time >= update_interval:
            try:
                frame = frames[frame_idx % len(frames)]
                status_text = f"{frame} **{status_prefix}**\n\nСгенерировано символов: `{len(full_text)}`"
                await processing_msg.edit_text(status_text, parse_mode="Markdown")
                frame_idx += 1
            except TelegramBadRequest:
                pass
            last_update_time = current_time

    return full_text.strip()
