import urllib.parse
from typing import Optional, List, Tuple
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.config import get_settings

def get_search_results_inline_kb(count: int) -> InlineKeyboardMarkup:
    """Builds a dynamic inline keyboard with numeric buttons for search results."""
    builder = InlineKeyboardBuilder()
    for i in range(1, count + 1):
        builder.button(text=str(i), callback_data=f"doc:{i-1}")
    builder.adjust(5)
    return builder.as_markup()

def get_note_actions_keyboard(
    filename: str,
    category: str = "",
    note_id: Optional[int] = None,
    vault: Optional[str] = None
) -> InlineKeyboardMarkup:
    """
    Builds interactive action keyboard for saved notes:
    - 🔗 «Открыть в Obsidian» (obsidian:// URL)
    - 📥 «Скачать .md» (callback)
    - 🗑 «Удалить» (callback with confirmation)
    """
    settings = get_settings()
    vault_name = vault or settings.OBSIDIAN_VAULT_NAME or "Notes"

    if category and category != "Root":
        file_path = f"{category.strip('/')}/{filename}"
    else:
        file_path = filename

    encoded_vault = urllib.parse.quote(vault_name)
    encoded_file = urllib.parse.quote(file_path)
    obsidian_url = f"obsidian://open?vault={encoded_vault}&file={encoded_file}"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔗 Открыть в Obsidian",
            url=obsidian_url
        )
    )

    cb_id = str(note_id) if note_id is not None else "0"
    builder.row(
        InlineKeyboardButton(
            text="📥 Скачать .md",
            callback_data=f"dl_note:{cb_id}"
        ),
        InlineKeyboardButton(
            text="🗑 Удалить",
            callback_data=f"del_note:{cb_id}"
        )
    )
    return builder.as_markup()

def get_note_fallback_keyboard(note_id: Optional[int] = None) -> InlineKeyboardMarkup:
    """
    Fallback inline keyboard with download and delete buttons, used when
    Telegram Bot API rejects custom URL schemes (obsidian://) with BUTTON_URL_INVALID.
    """
    builder = InlineKeyboardBuilder()
    cb_id = str(note_id) if note_id is not None else "0"
    builder.row(
        InlineKeyboardButton(
            text="📥 Скачать .md",
            callback_data=f"dl_note:{cb_id}"
        ),
        InlineKeyboardButton(
            text="🗑 Удалить",
            callback_data=f"del_note:{cb_id}"
        )
    )
    return builder.as_markup()

def get_delete_confirm_keyboard(note_id: int) -> InlineKeyboardMarkup:
    """Confirmation inline keyboard before deleting a note."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Да, удалить",
            callback_data=f"del_confirm:{note_id}"
        ),
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=f"del_cancel:{note_id}"
        )
    )
    return builder.as_markup()

def _truncate_callback_data(prefix: str, text: str, max_bytes: int = 64) -> str:
    """Safely truncates text so prefix + text fits strictly within max_bytes in UTF-8."""
    prefix_bytes = len(prefix.encode('utf-8'))
    allowed_bytes = max(0, max_bytes - prefix_bytes)
    encoded = text.encode('utf-8')
    if len(encoded) <= allowed_bytes:
        return text
    return encoded[:allowed_bytes].decode('utf-8', errors='ignore')

def get_tags_keyboard(tags_with_counts: List[Tuple[str, int]], max_tags: int = 20) -> InlineKeyboardMarkup:
    """Builds an inline keyboard listing top tags with note counts."""
    builder = InlineKeyboardBuilder()
    for tag, count in tags_with_counts[:max_tags]:
        # Telegram callback data limit is 64 bytes
        safe_tag = _truncate_callback_data("tag:", tag, max_bytes=64)
        builder.button(text=f"#{tag} ({count})", callback_data=f"tag:{safe_tag}")
    builder.adjust(2)
    return builder.as_markup()

