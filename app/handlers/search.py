from aiogram import Router, F
from aiogram.types import Message, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.database.db import search_notes, get_recent_context
from app.services.llm import answer_question
from app.services.s3_storage import S3StorageService
from app.keyboards.reply import get_cancel_keyboard, get_main_keyboard, get_numbers_kb

router = Router()
storage = S3StorageService()

class SearchAIState(StatesGroup):
    waiting_for_input = State()
    waiting_for_file_number = State()

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
        
        await processing_msg.delete()
        
        if not answer or not str(answer).strip():
            answer = "❌ ИИ вернул пустой ответ. Возможно, локальная модель не запущена, перегружена или произошла ошибка генерации."
            
        await message.answer(answer, reply_markup=get_main_keyboard())
        await state.clear()
        
    else:
        status_msg = await message.answer("🔍 Сканирую записи Obsidian... подожди немного.")
        results = await search_notes(query)
        
        if not results:
            await status_msg.delete()
            await message.answer("❌ По твоему запросу ничего не найдено.", reply_markup=get_main_keyboard())
            await state.clear()
            return

        files_list = [r[0] for r in results]
        categories_list = [r[1] for r in results]
        
        response_text = f"🔍 Найдено заметок: **{len(results)}**\n\n"
        for i, (filename, category) in enumerate(results, 1):
            response_text += f"{i}. `[{category}]` {filename}\n"
        response_text += "\nИспользуй клавиатуру с цифрами ниже для скачивания нужного файла."
        
        await status_msg.delete()
        await message.answer(response_text, reply_markup=get_numbers_kb(len(files_list)))
        
        await state.update_data(
            current_files=files_list, 
            current_categories=categories_list
        )
        await state.set_state(SearchAIState.waiting_for_file_number)

@router.message(SearchAIState.waiting_for_file_number, F.text.isdigit())
async def handle_file_selection(message: Message, state: FSMContext):
    data = await state.get_data()
    files = data.get("current_files", [])
    categories = data.get("current_categories", [])
    
    idx = int(message.text) - 1
    if 0 <= idx < len(files):
        filename = files[idx]
        category = categories[idx]
        
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
            await message.answer("❌ Не удалось скачать файл из SeaweedFS.", reply_markup=get_main_keyboard())
            await state.clear()
    else:
        await message.answer("❌ Неверный номер. Выбери число из списка выше.")