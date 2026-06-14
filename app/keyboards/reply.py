from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Отправить заметку"), KeyboardButton(text="🔍 Поиск / 🤖 ИИ")],
        ],
        resize_keyboard=True
    )

def get_cancel_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 В главное меню")]],
        resize_keyboard=True
    )

def get_numbers_kb(count: int):
    builder = ReplyKeyboardBuilder()
    for i in range(1, count + 1):
        builder.button(text=str(i))
    builder.button(text="🔙 В главное меню")
    builder.adjust(5)
    return builder.as_markup(resize_keyboard=True)

def get_back_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 В главное меню")]],
        resize_keyboard=True
    )