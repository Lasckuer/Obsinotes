from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Отправить заметку"), KeyboardButton(text="🤖 Спросить ИИ")],
            [KeyboardButton(text="📁 Мои файлы"), KeyboardButton(text="🔍 Найти / Теги")],
            [KeyboardButton(text="🎨 Создать холст (Canvas)")]
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

def get_numbers_kb(count: int):
    """Клавиатура с цифрами для выбора файла"""
    builder = ReplyKeyboardBuilder()
    for i in range(1, count + 1):
        builder.button(text=str(i))
    builder.button(text="🔙 В главное меню")
    builder.adjust(5)
    return builder.as_markup(resize_keyboard=True)

def get_back_menu_kb():
    """Простая кнопка возврата, чтобы не перекрывать Inline-меню"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 В главное меню")]],
        resize_keyboard=True
    )