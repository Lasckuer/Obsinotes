from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

def get_categories_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="💡 Идеи", callback_data="category_Ideas")
    builder.button(text="📝 Заметки", callback_data="category_Notes")
    builder.button(text="⏰ Напоминания", callback_data="category_Reminders")
    builder.button(text="🔗 Ссылки", callback_data="category_Links")
    builder.button(text="🏋️ Тренировки", callback_data="category_Workouts")
    builder.button(text="💰 Финансы", callback_data="category_Finance")
    builder.button(text="🔙 В главное меню", callback_data="close_menu")
    
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()

def get_files_kb(files: list, category: str):
    builder = InlineKeyboardBuilder()
    for i, file in enumerate(files, start=1):
        builder.button(text=str(i), callback_data=f"file_{category}_{file}")
    builder.button(text="🔙 К темам", callback_data="back_to_categories")
    builder.adjust(5)
    return builder.as_markup()