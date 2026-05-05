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

def get_categories_files_kb():
    builder = InlineKeyboardBuilder()
    categories = [
        ("💡 Идеи", "Ideas"), ("📝 Заметки", "Notes"), 
        ("⏰ Напоминания", "Reminders"), ("🔗 Ссылки", "Links"), 
        ("🏋️ Тренировки", "Workouts"), ("💰 Финансы", "Finance")
    ]
    for text, cat in categories:
        builder.button(text=text, callback_data=f"cat:{cat}")
    builder.adjust(2)
    return builder.as_markup()

def get_files_kb(files: list, category: str):
    builder = InlineKeyboardBuilder()
    for i, _ in enumerate(files):
        builder.button(text=str(i+1), callback_data=f"f_{category}_{i}")
    
    builder.button(text="🔙 К темам", callback_data="back_to_categories")
    builder.adjust(5)
    return builder.as_markup()

def get_pagination_inline_kb(page: int, total_pages: int):
    builder = InlineKeyboardBuilder()
    
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"p:{page-1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"p:{page+1}"))
    
    if nav_buttons:
        builder.row(*nav_buttons)
        
    builder.row(InlineKeyboardButton(text="🔙 К папкам", callback_data="back_to_cats"))
    return builder.as_markup()