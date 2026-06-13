from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.database.db import search_notes, get_recent_context
from app.services.llm import answer_question
from app.keyboards.reply import get_cancel_keyboard, get_main_keyboard, get_numbers_kb
from app.handlers.files import FileBrowser

router = Router()

class SearchAIState(StatesGroup):
    waiting_for_input = State()

@router.message(F.text == "🔍 Поиск / 🤖 ИИ")
async def prompt_search_or_ai(message: Message, state: FSMContext):
    await message.answer(
        "Что нужно сделать?\n\n"
        "🔍 *Найти файл:* отправь слово или тег (например, `#linux` или `рецепт`).\n"
        "🤖 *Спросить ИИ:* задай вопрос о своих записях (поставь `?` в конце или начни со слов `как / что / расскажи / напомни`).",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(SearchAIState.waiting_for_input)

@router.message(SearchAIState.waiting_for_input, F.text)
async def handle_search_or_ai(message: Message, state: FSMContext):
    query = message.text.strip()
    
    ai_triggers = ("как", "что", "где", "когда", "почему", "зачем", "сколько", "какой", "какие", "расскажи", "напиши", "спроси", "напомни")
    is_question = query.endswith("?") or query.lower().startswith(ai_triggers)
    
    if is_question:
        processing_msg = await message.answer("🤖 Изучаю твои записи...")
        context = await get_recent_context(20)
        answer = await answer_question(query, context)
        await processing_msg.edit_text(answer, reply_markup=get_main_keyboard())
        await state.clear()
        
    else:
        status_msg = await message.answer("🔍 Сканирую записи Obsidian... подожди немного.")
        
        results = await search_notes(query)
        
        if not results:
            await status_msg.edit_text("❌ По твоему запросу ничего не найдено.")
            await state.clear()
            return

        files_list = [r[0] for r in results]
        
        await message.answer(f"Найдено заметок: {len(results)}\n\nИспользуй клавиатуру с цифрами ниже для скачивания нужного файла.", reply_markup=get_main_keyboard())
        await message.answer("Доступные файлы для загрузки:", reply_markup=get_numbers_kb(len(files_list)))
        
        await state.update_data(current_files=files_list, current_page=1, current_category=results[0][1])
        await state.set_state(FileBrowser.browsing_files)
        
        await status_msg.delete()
        await message.answer(f"Найдено заметок: {len(results)}...")