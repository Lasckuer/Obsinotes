from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder

def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Returns the main persistent keyboard menu."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📝 Отправить заметку"),
                KeyboardButton(text="🔍 Поиск / 🤖 ИИ"),
                KeyboardButton(text="🔄 Полная синхронизация S3")
            ],
        ],
        resize_keyboard=True
    )

def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Returns a keyboard with a single 'Back to main menu' button."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 В главное меню")]],
        resize_keyboard=True
    )

# Alias for backwards compatibility
get_back_menu_kb = get_cancel_keyboard

def get_numbers_kb(count: int) -> ReplyKeyboardMarkup:
    """Builds a dynamic numeric selection keyboard for search results."""
    builder = ReplyKeyboardBuilder()
    for i in range(1, count + 1):
        builder.button(text=str(i))
    builder.button(text="🔙 В главное меню")
    builder.adjust(5)
    return builder.as_markup(resize_keyboard=True)