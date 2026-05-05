from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from app.keyboards.inline import get_categories_kb, get_files_kb
from app.keyboards.reply import get_main_keyboard
from app.services.yandex_disk import YaDiskService

router = Router()
ya_disk = YaDiskService()

@router.message(F.text == "📁 Мои файлы")
async def show_categories(message: Message):
    await message.answer("Выберите интересующую тему:", reply_markup=get_categories_kb())

@router.callback_query(F.data == "close_menu")
async def close_menu(callback: CallbackQuery):
    await callback.message.delete()
    await callback.message.answer("Вы в главном меню.", reply_markup=get_main_keyboard())

@router.callback_query(F.data == "back_to_categories")
async def back_to_categories(callback: CallbackQuery):
    await callback.message.edit_text("Выберите интересующую тему:", reply_markup=get_categories_kb())

@router.callback_query(F.data.startswith("category_"))
async def show_files(callback: CallbackQuery):
    category = callback.data.split("_")[1]
    files = await ya_disk.get_files(category)
    
    if not files:
        await callback.answer("В этой категории пока нет файлов.", show_alert=True)
        return
        
    text = f"Файлы в категории {category}:\n\n" + "\n".join([f"{i}. {f}" for i, f in enumerate(files, 1)])
    await callback.message.edit_text(text, reply_markup=get_files_kb(files, category))

@router.callback_query(F.data.startswith("file_"))
async def download_selected_file(callback: CallbackQuery):
    parts = callback.data.split("_", 2)
    category = parts[1]
    filename = parts[2]
    
    await callback.message.edit_text(f"Экспортирую файл {filename}...")
    
    file_bytes = await ya_disk.download_file(category, filename)
    input_file = BufferedInputFile(file_bytes, filename=filename)
    
    await callback.message.answer_document(input_file)
    await callback.message.delete()