from aiogram import Router, F
from aiogram.types import Message, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

from app.database.db import search_notes, get_recent_context
from app.services.llm import stream_answer_question
from app.services.s3_storage import S3StorageService
from app.keyboards.reply import get_cancel_keyboard, get_main_keyboard, get_numbers_kb
from app.utils.streaming import stream_to_message
from logger import logger

router = Router()
storage = S3StorageService()

class SearchAIState(StatesGroup):
    waiting_for_input = State()
    waiting_for_file_number = State()

AI_TRIGGERS = (
    "как", "что", "где", "когда", "почему", "зачем",
    "сколько", "какой", "какие", "расскажи", "напиши", "спроси", "напомни"
)

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
    is_question = query.endswith("?") or query.lower().startswith(AI_TRIGGERS)

    if is_question:
        status_msg = await message.answer("🤔 Изучаю заметки и формулирую ответ...")
        try:
            context_text = await get_recent_context(limit=15)
            stream_gen = stream_answer_question(query, context_text)
            full_text = await stream_to_message(status_msg, stream_gen, "ИИ изучает твои заметки и пишет ответ...")

            if full_text:
                try:
                    await status_msg.edit_text(full_text, parse_mode="Markdown")
                except TelegramBadRequest:
                    await status_msg.edit_text(full_text)
            else:
                await status_msg.edit_text("❌ Нейросеть вернула пустой ответ.")
        except Exception as e:
            logger.error(f"Ошибка ответа ИИ на вопрос: {e}")
            await status_msg.edit_text(f"❌ Ошибка: {e}")
        finally:
            await state.clear()
            await message.answer("Готово! Вы вернулись в главное меню 🏠", reply_markup=get_main_keyboard())

    else:
        status_msg = await message.answer("🔍 Сканирую записи Obsidian... подожди немного.")
        try:
            results = await search_notes(query, limit=25)

            if not results:
                await status_msg.delete()
                await message.answer("❌ По твоему запросу ничего не найдено.", reply_markup=get_main_keyboard())
                await state.clear()
                return

            files_list = [r[0] for r in results]
            categories_list = [r[1] for r in results]

            response_text = f"🔍 Найдено заметок: **{len(results)}**\n\n"
            for i, (filename, category) in enumerate(results, 1):
                clean_cat = category.replace("TelegramBot/", "")
                response_text += f"{i}. `[{clean_cat}]` {filename}\n"
            response_text += "\nИспользуй клавиатуру с цифрами ниже для скачивания нужного файла."

            await status_msg.delete()
            await message.answer(response_text, reply_markup=get_numbers_kb(len(files_list)))

            await state.update_data(
                current_files=files_list, 
                current_categories=categories_list
            )
            await state.set_state(SearchAIState.waiting_for_file_number)
        except Exception as e:
            logger.error(f"Ошибка поиска: {e}")
            await status_msg.edit_text(f"❌ Ошибка поиска: {e}")
            await state.clear()

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
        try:
            content = await storage.download_file(category, filename)
            await status_msg.delete()

            if content:
                await message.answer_document(
                    BufferedInputFile(content, filename=filename), 
                    reply_markup=get_main_keyboard()
                )
            else:
                await message.answer("❌ Не удалось скачать файл из хранилища.", reply_markup=get_main_keyboard())
        except Exception as e:
            logger.error(f"Ошибка скачивания файла: {e}")
            await status_msg.edit_text(f"❌ Ошибка скачивания: {e}")
        finally:
            await state.clear()
    else:
        await message.answer("❌ Неверный номер. Выбери число из списка выше.")