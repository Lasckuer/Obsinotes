from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from app.services.s3_storage import S3StorageService
from app.keyboards.inline import get_categories_files_kb, get_pagination_inline_kb
from app.keyboards.reply import get_back_menu_kb, get_numbers_kb, get_main_keyboard
from aiogram.fsm.state import State, StatesGroup

router = Router()
storage = S3StorageService()

class FileBrowser(StatesGroup):
    choosing_category = State()
    browsing_files = State()

@router.message(F.text == "📁 Мои файлы")
async def list_categories(message: Message, state: FSMContext):
    await state.set_state(FileBrowser.choosing_category)
    await message.answer("Управление меню:", reply_markup=get_back_menu_kb())
    await message.answer("Выбери папку:", reply_markup=get_categories_files_kb())

@router.callback_query(F.data.startswith("cat:"))
async def select_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":")[1]
    await state.update_data(current_category=category, current_categories=None)
    await state.set_state(FileBrowser.browsing_files)
    await show_files_page(callback, state, category, page=1)

async def show_files_page(event: Message | CallbackQuery, state: FSMContext, category: str, page: int):
    files = await storage.get_files(category)
    
    if not files:
        if isinstance(event, Message):
            await event.answer(f"В папке {category} пока нет заметок.")
        else:
            await event.message.edit_text(f"В папке {category} пока нет заметок.", reply_markup=get_categories_files_kb())
        return

    items_per_page = 10
    total_pages = (len(files) + items_per_page - 1) // items_per_page
    start_idx = (page - 1) * items_per_page
    end_idx = start_idx + items_per_page
    page_files = files[start_idx:end_idx]

    await state.update_data(current_files=files, current_page=page)

    text = f"📂 Папка: **{category}** (Страница {page}/{total_pages})\n\n"
    for i, filename in enumerate(page_files, 1):
        text += f"{i}. {filename}\n"
    text += "\nОтправь номер файла цифрой, чтобы скачать его."

    if isinstance(event, Message):
        await event.answer(text, reply_markup=get_numbers_kb(len(page_files)))
    else:
        await event.message.edit_text(text, reply_markup=get_pagination_inline_kb(page, total_pages))

@router.message(FileBrowser.browsing_files, F.text.isdigit())
async def handle_file_selection(message: Message, state: FSMContext):
    data = await state.get_data()
    page, files = data.get("current_page", 1), data.get("current_files", [])
    
    idx = (page - 1) * 10 + int(message.text) - 1
    if 0 <= idx < len(files):
        filename = files[idx]
        
        search_categories = data.get("current_categories")
        category = search_categories[idx] if search_categories else data.get("current_category")
        
        status_msg = await message.answer(f"⏳ Скачиваю: `{filename}`...")
        content = await storage.download_file(category, filename)
        
        await status_msg.delete()
        if content:
            await message.answer_document(
                BufferedInputFile(content, filename=filename), 
                reply_markup=get_main_keyboard()
            )
            await state.clear()
        else:
            await message.answer("❌ Не удалось скачать файл из SeaweedFS.")
    else:
        await message.answer("❌ Неверный номер. Выбери число из списка выше.")