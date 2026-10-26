import re
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

from app.database.db import (
    search_notes,
    get_recent_context,
    search_rag_context,
    get_note_by_file_and_cat,
    get_total_notes_count
)
from app.services.llm import stream_answer_question, ask_about_notes
from app.services.s3_storage import S3StorageService
from app.keyboards.reply import (
    get_cancel_keyboard,
    get_main_keyboard,
    get_numbers_kb,
    MAIN_MENU_BUTTONS,
    CANCEL_BUTTONS,
    ALL_NAVIGATION_BUTTONS
)
from app.keyboards.inline import get_search_results_inline_kb
from app.utils.streaming import stream_to_message
from logger import logger

from collections import OrderedDict

router = Router()
storage = S3StorageService()

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

@router.message(F.text.in_({"🔍 Поиск заметок", "Поиск заметок"}))
async def prompt_search_only(message: Message, state: FSMContext):
    await state.clear()
    total_notes = await get_total_notes_count()
    if total_notes == 0:
        await message.answer(
            "⚠️ В локальной базе данных пока нет заметок.\n\n"
            "Выполните синхронизацию с S3 (кнопка 🔄 Синхронизация S3), чтобы загрузить файлы.",
            reply_markup=get_main_keyboard()
        )
        return

    await message.answer(
        "🔍 *Поиск по заметкам Obsidian*\n\n"
        "Отправьте ключевые слова, фразу или тег (например: `#рецепты`, `настройка docker`, `пароли`):",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(SearchAIState.waiting_for_input)
    await state.update_data(search_mode="search")

@router.message(F.text.in_({"🤖 Спросить ИИ", "Спросить ИИ"}))
async def prompt_ai_only(message: Message, state: FSMContext):
    await state.clear()
    total_notes = await get_total_notes_count()
    if total_notes == 0:
        await message.answer(
            "⚠️ В локальной базе данных пока нет заметок.\n\n"
            "Сначала сохраните заметки или выполните синхронизацию с S3.",
            reply_markup=get_main_keyboard()
        )
        return

    from app.config import get_settings
    provider_name = get_settings().active_ai_provider_display
    await message.answer(
        f"🤖 *Задайте вопрос ({provider_name})*\n\n"
        "Спросите что угодно по вашей базе заметок Obsidian.\n"
        "Например:\n"
        "• _Что я планировал сделать по ремонту?_\n"
        "• _Какие книги я хотел прочитать?_\n"
        "• _Напомни рецепт шарлотки_",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(SearchAIState.waiting_for_input)
    await state.update_data(search_mode="ai")

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
    await state.update_data(search_mode="auto")

@router.message(
    SearchAIState.waiting_for_input,
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_(ALL_NAVIGATION_BUTTONS)
)
async def handle_search_or_ai(message: Message, state: FSMContext):
    query = message.text.strip()
    data = await state.get_data()
    mode = data.get("search_mode", "auto")

    if mode == "ai":
        is_question = True
    elif mode == "search":
        is_question = False
    else:
        is_question = query.endswith("?") or bool(AI_TRIGGER_PATTERN.match(query))

    if is_question:
        status_msg = await message.answer("🤔 Изучаю заметки и формулирую ответ...")
        try:
            rag_notes = await search_rag_context(query, limit=5)
            context_lines = []
            source_files = []
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

                if fname and (fname, cat) not in source_files:
                    source_files.append((fname, cat))

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
                has_no_info = "нет информации" in full_text.lower()
                valid_sources = source_files if (source_files and not has_no_info) else []

                if valid_sources:
                    sources_str = "\n\n📚 *Использованные заметки:*\n" + "\n".join(
                        f"{i}. `[{cat.replace('TelegramBot/', '') or 'Корень'}]` {fn}"
                        for i, (fn, cat) in enumerate(valid_sources, 1)
                    )
                    full_text += sources_str

                text_to_show = full_text if len(full_text) <= 4000 else full_text[:3990] + "..."

                inline_kb = get_search_results_inline_kb(len(valid_sources)) if valid_sources else None

                try:
                    await status_msg.edit_text(text_to_show, reply_markup=inline_kb, parse_mode="Markdown")
                except TelegramBadRequest as e:
                    err = str(e).lower()
                    if "message is not modified" not in err:
                        try:
                            clean_txt = text_to_show.replace("**", "").replace("*", "").replace("`", "")
                            await status_msg.edit_text(clean_txt, reply_markup=inline_kb)
                        except TelegramBadRequest as e2:
                            if "message is not modified" not in str(e2).lower():
                                pass
                except Exception:
                    pass

                if valid_sources:
                    chat = getattr(status_msg, "chat", None)
                    chat_id = getattr(chat, "id", None)
                    msg_id = getattr(status_msg, "message_id", None)
                    files_list = [s[0] for s in valid_sources]
                    cats_list = [s[1] for s in valid_sources]
                    if chat_id is not None and msg_id is not None:
                        search_results_cache.set(chat_id, msg_id, files_list, cats_list)
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
                total_notes = await get_total_notes_count()
                try:
                    await status_msg.delete()
                except Exception:
                    pass

                if total_notes == 0:
                    await message.answer(
                        "⚠️ В локальной базе данных пока нет заметок.\n\n"
                        "Выполните синхронизацию с S3 (кнопка 🔄 Синхронизация S3), чтобы загрузить файлы.",
                        reply_markup=get_main_keyboard()
                    )
                else:
                    await message.answer("❌ По твоему запросу ничего не найдено.", reply_markup=get_main_keyboard())
                await state.clear()
                return

            files_list = [r[0] for r in results]
            categories_list = [r[1] for r in results]

            response_text = f"🔍 Найдено заметок: **{len(results)}**\n\n"
            for i, r in enumerate(results, 1):
                filename = r[0]
                category = r[1]
                snippet = getattr(r, "snippet", "") if hasattr(r, "snippet") else (r[2] if len(r) > 2 else "")
                clean_cat = category.replace("TelegramBot/", "") if category else "Корень"
                if not clean_cat or clean_cat == "Root":
                    clean_cat = "Корень"
                response_text += f"{i}. `[{clean_cat}]` **{filename}**\n"
                if snippet:
                    clean_snip = snippet.replace("*", "").replace("`", "").strip()
                    response_text += f"   _{clean_snip}_\n"
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