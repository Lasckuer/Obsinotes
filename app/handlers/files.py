from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from app.services.yandex_disk import YaDiskService
from app.keyboards.inline import get_categories_files_kb, get_pagination_inline_kb
from app.keyboards.reply import get_back_menu_kb, get_numbers_kb, get_main_keyboard
from aiogram.fsm.state import State, StatesGroup

router = Router()
ya_disk = YaDiskService()

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
    await state.update_data(current_category=category)
    await state.set_state(FileBrowser.browsing_files)
    await show_files_page(callback, state, category, page=1)
    await callback.answer()

async def show_files_page(event: Message | CallbackQuery, state: FSMContext, category: str, page: int):
    full_path = f"Notes/TelegramBot/{category}"

    if not await ya_disk.y.exists(full_path):
        await ya_disk.y.mkdir(full_path)
        return await event.answer(f"В папке {category} пока нет заметок.")

    files = await ya_disk.get_files(category)

    items_per_page = 10
    total_pages = (len(files) + items_per_page - 1) // items_per_page
    start_idx = (page - 1) * items_per_page
    current_page_files = files[start_idx : start_idx + items_per_page]

    await state.update_data(current_files=files, current_page=page)
    
    text = f"📂 Папка: {category} (Стр. {page}/{total_pages})\n\n"
    for i, file in enumerate(current_page_files, start=1):
        text += f"{i}. {file}\n"

    inline_kb = get_pagination_inline_kb(page, total_pages)
    reply_kb = get_numbers_kb(len(current_page_files))

    if isinstance(event, Message):
        await event.answer(text, reply_markup=inline_kb)
        await event.answer("Выбери номер файла:", reply_markup=reply_kb)
    else:
        await event.message.edit_text(text, reply_markup=inline_kb)
        await event.message.answer("Страница обновлена. Выбери номер:", reply_markup=reply_kb)

@router.callback_query(FileBrowser.browsing_files)
async def handle_pagination(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    category = data.get("current_category")
    
    if callback.data.startswith("p:"):
        new_page = int(callback.data.split(":")[1])
        await show_files_page(callback, state, category, new_page)
    elif callback.data == "back_to_cats":
        await state.set_state(FileBrowser.choosing_category)
        await callback.message.edit_text("Выбери папку:", reply_markup=get_categories_files_kb())
    await callback.answer()

@router.message(FileBrowser.browsing_files, F.text.isdigit())
async def handle_file_selection(message: Message, state: FSMContext):
    data = await state.get_data()
    page, files, category = data.get("current_page"), data.get("current_files"), data.get("current_category")
    
    idx = (page - 1) * 10 + int(message.text) - 1
    if 0 <= idx < len(files):
        filename = files[idx]
        await message.answer(f"⏳ Скачиваю: {filename}...")
        content = await ya_disk.download_file(category, filename)
        await message.answer_document(BufferedInputFile(content, filename=filename))
    else:
        await message.answer("Нет файла под таким номером.")

@router.message(F.text == "🔙 В главное меню")
async def cancel_browser(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Вы вернулись в главное меню.", reply_markup=get_main_keyboard())
