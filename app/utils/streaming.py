import time
from typing import AsyncGenerator
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest
from logger import logger

async def stream_to_message(
    processing_msg: Message,
    stream_generator: AsyncGenerator[str, None],
    status_prefix: str,
    update_interval: float = 1.8
) -> str:
    """
    Consumes an async stream of string tokens, updating the Telegram message
    periodically with the growing generated text.
    Throttled to 1.5–2.0 seconds (default 1.8s) to prevent Telegram rate limits.
    Falls back to plain text if Markdown parsing fails during streaming.
    Ensures the final text is displayed upon stream completion.
    Returns the accumulated full string.
    """
    full_text = ""
    last_displayed_text = ""
    last_update_time = time.time()

    async def _safe_update(text: str) -> None:
        nonlocal last_displayed_text
        if not text or text == last_displayed_text:
            return
        text_to_show = text if len(text) <= 4000 else text[:3990] + "..."
        try:
            await processing_msg.edit_text(text_to_show, parse_mode="Markdown")
            last_displayed_text = text_to_show
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "message is not modified" in err:
                last_displayed_text = text_to_show
            else:
                try:
                    await processing_msg.edit_text(text_to_show)
                    last_displayed_text = text_to_show
                except Exception:
                    pass
        except Exception:
            pass

    async for chunk in stream_generator:
        full_text += chunk
        current_time = time.time()

        if current_time - last_update_time >= update_interval:
            display_text = full_text.strip()
            if display_text and display_text != last_displayed_text:
                await _safe_update(display_text)
                last_update_time = current_time

    # Final update after stream finishes
    final_text = full_text.strip()
    if final_text and final_text != last_displayed_text:
        await _safe_update(final_text)

    return final_text
