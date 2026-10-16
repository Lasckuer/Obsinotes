import re
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

from app.database.db import search_notes, get_recent_context, search_rag_context, get_note_by_file_and_cat
from app.services.llm import stream_answer_question, ask_about_notes
from app.services.s3_storage import S3StorageService
from app.keyboards.reply import get_cancel_keyboard, get_main_keyboard, get_numbers_kb
from app.keyboards.inline import get_search_results_inline_kb
from app.utils.streaming import stream_to_message
from logger import logger

from collections import OrderedDict

router = Router()
storage = S3StorageService()

MAIN_MENU_BUTTONS = {
    "📝 Отправить заметку",
    "🔍 Поиск / 🤖 ИИ",
    "🔄 Полная синхронизация S3",
}

CANCEL_BUTTONS = {
    "🔙 В главное меню",
    "В главное меню",
    "в главное меню",
    "❌ Отмена",
    "❌ отмена",
    "отмена",
    "Отмена",
    "ОТМЕНА",
    "отменить",
    "Отменить",
}

ALL_NAVIGATION_BUTTONS = MAIN_MENU_BUTTONS | CANCEL_BUTTONS

class SearchResultsCache:
    """Stores the latest search results by (chat_id, message_id) so inline buttons remain clickable."""
    def __init__(self, maxsize: int = 200):
        self._maxsize = maxsize
        self._cache: OrderedDict = OrderedDict()

    def set(self, chat_id: int, message_id: int, files: list, categories: list):
        key = (chat_id, message_id)
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = (files, categories)
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    def get(self, chat_id: int, message_id: int):
        return self._cache.get((chat_id, message_id))

search_results_cache = SearchResultsCache()

class SearchAIState(StatesGroup):
    waiting_for_input = State()
    waiting_for_file_number = State()

@router.message(Command("cancel"))
@router.message(F.text.in_(CANCEL_BUTTONS))
async def cmd_cancel_search(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Вы вернулись в главное меню.", reply_markup=get_main_keyboard())

AI_TRIGGERS = (
    "как", "что", "где", "когда", "почему", "зачем",
    "сколько", "кто", "куда", "откуда",
    "какой", "какая", "какое", "какие", "какую", "каком", "каких", "каким", "какими",
    "расскажи", "напиши", "спроси", "напомни", "объясни", "подскажи"
)

AI_TRIGGER_PATTERN = re.compile(
    r'^(?:' + '|'.join(AI_TRIGGERS) + r')(?!(?:-|\s+)(?:то|либо|нибудь)\b)\b',
    re.IGNORECASE
)

@router.message(F.text == "🔍 Поиск / 🤖 ИИ")
async def prompt_search_or_ai(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Что нужно сделать?\n\n"
        "🔍 *Найти файл:* отправь слово или тег (например, `#linux` или `рецепт`).\n"
        "🤖 *Спросить ИИ:* задай вопрос о своих записях (поставь `?` в конце или начни со слов `как / что / расскажи / напомни`).",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(SearchAIState.waiting_for_input)

@router.message(
    SearchAIState.waiting_for_input,
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_(ALL_NAVIGATION_BUTTONS)
)
async def handle_search_or_ai(message: Message, state: FSMContext):
    query = message.text.strip()
    is_question = query.endswith("?") or bool(AI_TRIGGER_PATTERN.match(query))

    if is_question:
        status_msg = await message.answer("🤔 Изучаю заметки и формулирую ответ...")
        try:
            rag_notes = await search_rag_context(query, limit=5)
            context_lines = []
            for n in rag_notes:
                if isinstance(n, dict):
                    cat = n.get("category", "")
                    fname = n.get("filename", "")
                    cont = n.get("content", "")
                    dt = n.get("date", "")
                elif isinstance(n, (list, tuple)):
                    if len(n) == 4:
                        fname, cat, cont, dt = n
                    else:
                        fname = ""
                        cat, cont, dt = n[0], n[1], n[2]
                else:
                    continue

                clean_cat = cat.replace("TelegramBot/", "") if cat else "Корень"
                if not clean_cat or clean_cat == "Root":
                    clean_cat = "Корень"
                prefix = f"[{dt}] [{clean_cat} / {fname}]" if fname else f"[{dt}] [{clean_cat}]"
                snippet = cont[:1000] + "..." if len(cont) > 1000 else cont
                context_lines.append(f"{prefix}: {snippet}\n\n")

            context_text = "".join(context_lines)

            stream_gen = stream_answer_question(query, context_text)
            full_text = await stream_to_message(status_msg, stream_gen, "ИИ изучает твои заметки и пишет ответ...")

            if full_text:
                text_to_show = full_text if len(full_text) <= 4000 else full_text[:3990] + "..."
                try:
                    await status_msg.edit_text(text_to_show, parse_mode="Markdown")
                except TelegramBadRequest as e:
                    err = str(e).lower()
                    if "message is not modified" not in err:
                        try:
                            await status_msg.edit_text(text_to_show)
                        except TelegramBadRequest as e2:
                            if "message is not modified" not in str(e2).lower():
                                pass
                except Exception:
                    pass
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
                clean_cat = category.replace("TelegramBot/", "") if category else "Корень"
                if not clean_cat or clean_cat == "Root":
                    clean_cat = "Корень"
                response_text += f"{i}. `[{clean_cat}]` {filename}\n"
            response_text += "\nНажмите номер файла ниже для скачивания или отправьте новый запрос:"

            try:
                await status_msg.delete()
            except Exception:
                pass

            inline_kb = get_search_results_inline_kb(len(files_list))
            try:
                results_msg = await message.answer(response_text, reply_markup=inline_kb, parse_mode="Markdown")
            except TelegramBadRequest:
                results_msg = await message.answer(response_text, reply_markup=inline_kb)

            chat = getattr(results_msg, "chat", None)
            chat_id = getattr(chat, "id", None)
            msg_id = getattr(results_msg, "message_id", None)
            if chat_id is not None and msg_id is not None:
                search_results_cache.set(
                    chat_id, msg_id, files_list, categories_list
                )

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
            if not content:
                note = await get_note_by_file_and_cat(filename, category)
                if note and note.get("content"):
                    content = note["content"].encode('utf-8')

            try:
                await status_msg.delete()
            except Exception:
                pass

            if content:
                await message.answer_document(
                    BufferedInputFile(content, filename=filename), 
                    reply_markup=get_main_keyboard()
                )
            else:
                await message.answer("❌ Не удалось скачать файл из хранилища.", reply_markup=get_main_keyboard())
        except Exception as e:
            logger.error(f"Ошибка скачивания файла: {e}")
            await message.answer(f"❌ Ошибка скачивания: {e}", reply_markup=get_main_keyboard())
        finally:
            await state.clear()
    else:
        await message.answer("❌ Неверный номер. Выбери число из списка выше или отправь новый запрос.")

@router.callback_query(F.data.startswith("doc:"))
async def handle_file_selection_callback(callback: CallbackQuery, state: FSMContext):
    try:
        idx = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка в данных запроса.", show_alert=True)
        return

    data = await state.get_data()
    files = data.get("current_files", [])
    categories = data.get("current_categories", [])

    msg = getattr(callback, "message", None)
    chat = getattr(msg, "chat", None)
    chat_id = getattr(chat, "id", None)
    msg_id = getattr(msg, "message_id", None)
    if (not files or idx >= len(files)) and chat_id is not None and msg_id is not None:
        cached = search_results_cache.get(chat_id, msg_id)
        if cached:
            files, categories = cached

    if not files or idx < 0 or idx >= len(files):
        await callback.answer("❌ Результат поиска устарел. Повторите поиск.", show_alert=True)
        return

    filename = files[idx]
    category = categories[idx]

    await callback.answer(f"⏳ Скачиваю {filename}...")
    try:
        content = await storage.download_file(category, filename)
        if not content:
            note = await get_note_by_file_and_cat(filename, category)
            if note and note.get("content"):
                content = note["content"].encode('utf-8')

        if content:
            if callback.message:
                await callback.message.answer_document(
                    BufferedInputFile(content, filename=filename),
                    reply_markup=get_main_keyboard()
                )
        else:
            if callback.message:
                await callback.message.answer(
                    "❌ Не удалось скачать файл из хранилища.",
                    reply_markup=get_main_keyboard()
                )
    except Exception as e:
        logger.error(f"Ошибка скачивания файла через callback: {e}")
        if callback.message:
            await callback.message.answer(f"❌ Ошибка скачивания: {e}", reply_markup=get_main_keyboard())
    finally:
        await state.clear()

@router.message(
    SearchAIState.waiting_for_file_number,
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_(ALL_NAVIGATION_BUTTONS)
)
async def handle_new_query_in_file_selection(message: Message, state: FSMContext):
    """Allows submitting a new search query or question directly without returning to main menu."""
    await handle_search_or_ai(message, state)

ask_about_notes = stream_answer_question