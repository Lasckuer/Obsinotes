from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Отправить заметку")],
            [KeyboardButton(text="📁 Мои файлы"), KeyboardButton(text="🔍 Найти / Теги")]
        ],
        resize_keyboard=True
    )

def get_cancel_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔙 В главное меню")]
        ],
        resize_keyboard=True
    )