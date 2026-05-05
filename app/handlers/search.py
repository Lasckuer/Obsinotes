from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.database.db import search_notes
from app.keyboards.reply import get_cancel_keyboard, get_main_keyboard
from app.keyboards.inline import get_files_kb

router = Router()

class SearchState(StatesGroup):
    waiting_for_query = State()

@router.message(F.text == "🔍 Найти / Теги")
async def prompt_search(message: Message, state: FSMContext):
    await message.answer(
        "Введите слово для поиска в тексте или отправьте тег (например, `#linux` или `#ideas`):",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(SearchState.waiting_for_query)

@router.message(SearchState.waiting_for_query, F.text)
async def handle_search(message: Message, state: FSMContext):
    query = message.text.strip()
    is_tag = query.startswith("#")
    search_term = query.replace("#", "")
    
    results = await search_notes(search_term, is_tag)
    
    if not results:
        await message.answer("Ничего не найдено.", reply_markup=get_main_keyboard())
        await state.clear()
        return

    text = f"Найдено совпадений: {len(results)}\n\n"
    files = []
    category_map = {}
    
    for filename, category in results:
        text += f"📂 {category} / {filename}\n"
        files.append(filename)
        category_map[filename] = category

    await message.answer(text, reply_markup=get_main_keyboard())
    await state.clear()