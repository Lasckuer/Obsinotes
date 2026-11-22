from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder

MAIN_MENU_BUTTONS = {
    # Active main menu buttons
    "📝 Новая заметка",
    "🔍 Поиск заметок",
    "🤖 Спросить ИИ",
    "⏰ Напоминания",
    "⚙️ Настройки",
    # Legacy buttons for backwards compatibility
    "🏷️ Каталог тегов",
    "📅 Ежедневник",
    "💰 Расходы",
    "🔄 Синхронизация S3",
    "⚙️ Статус и справка",
    "📝 Отправить заметку",
    "🔍 Поиск / 🤖 ИИ",
    "🔄 Полная синхронизация S3",
}

CANCEL_BUTTONS = {
    "🔙 В главное меню",
    "В главное меню",
    "в главное меню",
    "❌ Отмена",
    "❌ отмена",
    "отмена",
    "Отмена",
    "ОТМЕНА",
    "отменить",
    "Отменить",
}

ALL_NAVIGATION_BUTTONS = MAIN_MENU_BUTTONS | CANCEL_BUTTONS

def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Returns the main persistent keyboard menu."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Новая заметка"), KeyboardButton(text="🔍 Поиск заметок")],
            [KeyboardButton(text="🤖 Спросить ИИ"), KeyboardButton(text="⏰ Напоминания")],
            [KeyboardButton(text="⚙️ Настройки")],
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