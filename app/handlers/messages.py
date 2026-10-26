import os
import io
import time
import uuid
import datetime
import asyncio
import re
import urllib.parse
from typing import Tuple, Optional, Union, Dict, Any, List

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from docx import Document

from app.config import get_settings
from app.services.llm import (
    get_rephrased_filename, 
    process_text,
    stream_process_examiner_text, 
    stream_summarize_document, 
    transcribe_audio, 
    generate_semantic_filename,
    test_ai_connection
)
from app.services.s3_storage import S3StorageService
from app.services.scraper import extract_urls, fetch_url_content
from app.database.db import (
    add_reminder,
    add_expense,
    get_expenses_summary,
    add_note_log,
    upsert_note_log,
    get_note_by_id,
    get_note_by_file_and_cat,
    get_all_tags_with_counts,
    get_notes_by_tag,
    get_recent_notes_for_linking,
    get_all_notes_metadata,
    delete_notes,
    commit_db,
    get_total_notes_count,
    get_today_notes
)
from app.keyboards.reply import (
    get_cancel_keyboard,
    get_main_keyboard,
    MAIN_MENU_BUTTONS,
    CANCEL_BUTTONS,
    ALL_NAVIGATION_BUTTONS
)
from app.keyboards.inline import (
    get_note_actions_keyboard,
    get_note_fallback_keyboard,
    get_delete_confirm_keyboard,
    get_tags_keyboard,
    get_search_results_inline_kb,
    get_status_keyboard,
    get_daily_format_keyboard
)
from app.handlers.search import search_results_cache
from app.utils.streaming import stream_to_message
from logger import logger

router = Router()
storage = S3StorageService()


def is_bot_command(text: str) -> bool:
    if not text:
        return False
    stripped = text.strip()
    first_word = stripped.split(None, 1)[0]
    return bool(re.match(r"^/[a-zA-Z0-9_]+(@\w+)?$", first_word))

def parse_expense_amount(val: Any) -> float:
    """Robustly extracts float amount from LLM outputs (handles numbers, strings with currency, commas)."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val) if val > 0 else 0.0
    if isinstance(val, str):
        cleaned = val.strip().replace(" ", "")
        if "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(",", ".")
        match = re.search(r"\d+(?:\.\d+)?", cleaned)
        if match:
            try:
                amt = float(match.group(0))
                return amt if amt > 0 else 0.0
            except ValueError:
                return 0.0
    return 0.0

async def safe_reply_with_note_actions(
    target: Message,
    text: str,
    filename: str,
    category: str,
    note_id: Optional[int],
    is_edit: bool = False
) -> Message:
    """
    Safely delivers the note completion message with interactive action buttons:
    - First attempts with standard get_note_actions_keyboard (including obsidian:// URL button).
    - If Telegram Bot API rejects obsidian:// with BUTTON_URL_INVALID, falls back to
      get_note_fallback_keyboard (Download & Delete buttons) and embeds Markdown link into text.
    - Gracefully recovers from any Markdown parsing errors by falling back to unformatted text.
    """
    settings = get_settings()
    vault_name = settings.OBSIDIAN_VAULT_NAME or "Notes"
    actions_kb = get_note_actions_keyboard(filename, category, note_id, vault=vault_name)
    fallback_kb = get_note_fallback_keyboard(note_id)

    # 1. Try sending with full action keyboard and Markdown
    try:
        if is_edit:
            return await target.edit_text(text, reply_markup=actions_kb, parse_mode="Markdown")
        else:
            return await target.answer(text, reply_markup=actions_kb, parse_mode="Markdown")
    except TelegramBadRequest as e:
        logger.debug(f"Initial attempt with actions keyboard failed ({e}). Retrying with plain text...")

    # 2. Try actions_kb without Markdown (in case issue was Markdown formatting)
    try:
        clean_text = text.replace("**", "").replace("`", "").replace("_", "")
        if is_edit:
            return await target.edit_text(clean_text, reply_markup=actions_kb)
        else:
            return await target.answer(clean_text, reply_markup=actions_kb)
    except TelegramBadRequest as e:
        logger.debug(f"Actions keyboard with plain text failed ({e}). Using fallback keyboard...")

    # 3. If actions_kb failed (e.g. BUTTON_URL_INVALID), use fallback_kb (Download & Delete)
    if category and category != "Root":
        file_path = f"{category.strip('/')}/{filename}"
    else:
        file_path = filename
    encoded_vault = urllib.parse.quote(vault_name)
    encoded_file = urllib.parse.quote(file_path)
    obsidian_url = f"obsidian://open?vault={encoded_vault}&file={encoded_file}"
    text_with_obsidian = f"{text}\n\n🔗 [Открыть в Obsidian]({obsidian_url})"

    try:
        if is_edit:
            return await target.edit_text(text_with_obsidian, reply_markup=fallback_kb, parse_mode="Markdown")
        else:
            return await target.answer(text_with_obsidian, reply_markup=fallback_kb, parse_mode="Markdown")
    except TelegramBadRequest:
        clean_text_fallback = f"{text.replace('**', '').replace('`', '').replace('_', '')}\n\n🔗 Открыть в Obsidian: {obsidian_url}"
        if is_edit:
            return await target.edit_text(clean_text_fallback, reply_markup=fallback_kb)
        else:
            return await target.answer(clean_text_fallback, reply_markup=fallback_kb)

class NoteState(StatesGroup):
    waiting_for_text = State()

class DailyState(StatesGroup):
    waiting_for_text = State()


@router.message(Command("cancel"))
@router.message(F.text.in_(CANCEL_BUTTONS))
async def cmd_back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Вы вернулись в главное меню.", reply_markup=get_main_keyboard())


@router.message(F.text.in_({"📝 Новая заметка", "📝 Отправить заметку"}))
async def prompt_note(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Напиши текст, скинь ссылку, документ (.pdf, .docx, .txt, .md), голосовое сообщение или фото:", 
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(NoteState.waiting_for_text)


@router.message(StateFilter(NoteState.waiting_for_text, None), F.photo)
async def handle_photo(message: Message, state: FSMContext, bot: Bot):
    processing_msg = await message.answer("Сохраняю фото...")

    try:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)

        filename_img = f"img_{uuid.uuid4().hex[:8]}.jpg"
        s3_img_folder = "TelegramBot/Photos"
        await storage.upload_file(s3_img_folder, filename_img, downloaded_file.read())

        caption = message.caption or ""
        md_content = f"![[{filename_img}]]\n"
        text_to_process = f"{md_content}\n{caption}"

        category = "Photos"
        tags_str = ""
        corrected_text = text_to_process
        base_name = f"photo_{uuid.uuid4().hex[:4]}"

        if caption:
            processed = await process_text(caption)
            category = processed.get("category", "Photos")
            raw_tags = processed.get("tags", [])
            if isinstance(raw_tags, str):
                tags_list = [t.strip().lstrip('#') for t in raw_tags.split(",")]
            elif isinstance(raw_tags, list):
                tags_list = [str(t).strip().lstrip('#') for t in raw_tags]
            else:
                tags_list = []
            tags_str = ", ".join([t for t in tags_list if t])

            if processed.get("corrected_text"):
                corrected_text = f"{md_content}\n{processed.get('corrected_text')}"

            if processed.get("filename"):
                base_name = processed.get("filename").strip()

        md_filename = base_name if base_name.endswith('.md') else f"{base_name}.md"
        md_filename = await get_rephrased_filename(category, md_filename, caption or "Фотография")
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d")

        final_md = f"---\ntags: [{tags_str}]\ndate: {timestamp}\n---\n\n{corrected_text}"
        s3_folder = f"TelegramBot/{category}"

        await storage.upload_file(s3_folder, md_filename, final_md.encode('utf-8'))
        note_id = await add_note_log(filename=md_filename, category=s3_folder, tags=tags_str, content=final_md)
        await safe_reply_with_note_actions(
            target=processing_msg,
            text=f"✅ Фото сохранено как `{s3_folder}/{md_filename}`",
            filename=md_filename,
            category=s3_folder,
            note_id=note_id,
            is_edit=True
        )
    except Exception as e:
        logger.error(f"Ошибка при обработке фото: {e}")
        await processing_msg.edit_text(f"❌ Произошла ошибка при сохранении фото: {e}")
    finally:
        await state.clear()
        await message.answer("Готово! Вы вернулись в главное меню 🏠", reply_markup=get_main_keyboard())



@router.message(StateFilter(NoteState.waiting_for_text, None), F.voice | F.audio)
async def handle_audio(message: Message, state: FSMContext, bot: Bot):
    processing_msg = await message.answer("🎧 Распознаю аудио...")
    file_id = message.voice.file_id if message.voice else message.audio.file_id
    file_info = await bot.get_file(file_id)
    downloaded_file = await bot.download_file(file_info.file_path)

    ext = ".ogg"
    if message.audio:
        orig_name = message.audio.file_name or ""
        if orig_name and "." in orig_name:
            ext = os.path.splitext(orig_name)[1].lower()
        elif file_info.file_path and "." in file_info.file_path:
            ext = os.path.splitext(file_info.file_path)[1].lower()
        else:
            ext = ".mp3"
    elif message.voice:
        ext = ".ogg"
    elif file_info.file_path and "." in file_info.file_path:
        file_ext = os.path.splitext(file_info.file_path)[1].lower()
        if file_ext:
            ext = file_ext

    if ext == ".oga":
        ext = ".ogg"

    temp_filename = f"temp_{uuid.uuid4().hex[:8]}{ext}"
    with open(temp_filename, "wb") as f:
        f.write(downloaded_file.read())

    try:
        text = await transcribe_audio(temp_filename)
    except Exception as e:
        logger.error(f"Ошибка транскрибации: {e}")
        text = ""
    finally:
        if os.path.exists(temp_filename):
            try:
                os.remove(temp_filename)
            except OSError:
                pass

    if not text:
        await processing_msg.edit_text("❌ Не удалось распознать аудио.")
        await state.clear()
        await message.answer("Вы вернулись в главное меню 🏠", reply_markup=get_main_keyboard())
        return

    await process_note_text(message, state, text, processing_msg)


MENU_BUTTONS = ALL_NAVIGATION_BUTTONS

@router.message(
    StateFilter(NoteState.waiting_for_text),
    F.text,
    ~F.text.in_(ALL_NAVIGATION_BUTTONS),
    ~F.text.func(is_bot_command)
)
@router.message(
    StateFilter(None),
    F.text,
    ~F.text.in_(ALL_NAVIGATION_BUTTONS),
    ~F.text.func(is_bot_command)
)
async def handle_text(message: Message, state: FSMContext):
    urls = extract_urls(message.text)
    url_content = ""

    if urls:
        target_url = urls[0].rstrip(".,;:!?)>]}")
        processing_msg = await message.answer(f"🌐 Загружаю содержимое ссылки: {target_url}...")
        url_content = await fetch_url_content(target_url)
        if url_content:
            await processing_msg.edit_text("🧠 Анализирую веб-страницу и текст заметки...")
        else:
            await processing_msg.edit_text("🧠 Анализирую текст...")
    else:
        processing_msg = await message.answer("🧠 Анализирую текст...")

    await process_note_text(message, state, message.text, processing_msg, url_content=url_content)


async def append_monthly_expense(amount: float, comment: str):
    """Appends an expense entry to the monthly Obsidian note TelegramBot/Finance/{YYYY-MM}.md in S3."""
    now = datetime.datetime.now()
    year_month = now.strftime("%Y-%m")
    date_time_str = now.strftime("%Y-%m-%d %H:%M")
    filename = f"{year_month}.md"
    folder = "TelegramBot/Finance"

    existing_bytes = await storage.download_file(folder, filename)
    new_entry = f"- {date_time_str}: **{amount:,.2f}** — {comment}"
    if existing_bytes:
        existing_text = existing_bytes.decode('utf-8', errors='ignore').rstrip()
        updated_content = f"{existing_text}\n{new_entry}\n"
    else:
        updated_content = (
            f"---\n"
            f"tags: [finance, расходы]\n"
            f"month: {year_month}\n"
            f"---\n\n"
            f"# Расходы за {year_month}\n\n"
            f"{new_entry}\n"
        )

    await storage.upload_file(folder, filename, updated_content.encode('utf-8'))
    await upsert_note_log(
        filename=filename,
        category=folder,
        tags="finance, расходы",
        content=updated_content
    )


async def append_daily_note(text: str) -> Tuple[str, str, Optional[int]]:
    """Appends a timestamped note/todo to TelegramBot/Daily/{YYYY-MM-DD}.md in S3."""
    now = datetime.datetime.now()
    today_date = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    filename = f"{today_date}.md"
    folder = "TelegramBot/Daily"

    existing_bytes = await storage.download_file(folder, filename)
    new_entry = f"- {time_str}: {text}"
    if existing_bytes:
        existing_text = existing_bytes.decode('utf-8', errors='ignore').rstrip()
        updated_content = f"{existing_text}\n{new_entry}\n"
    else:
        updated_content = (
            f"---\n"
            f"tags: [daily]\n"
            f"date: {today_date}\n"
            f"---\n\n"
            f"# Ежедневник — {today_date}\n\n"
            f"{new_entry}\n"
        )

    await storage.upload_file(folder, filename, updated_content.encode('utf-8'))
    await upsert_note_log(
        filename=filename,
        category=folder,
        tags="daily",
        content=updated_content
    )

    note = await get_note_by_file_and_cat(filename, folder)
    note_id = note["id"] if note else None
    return filename, folder, note_id


async def process_note_text(
    message: Message,
    state: FSMContext,
    text: str,
    processing_msg: Message,
    url_content: str = "",
    is_document: bool = False
):
    try:
        if is_document or len(text) > 1000:
            await processing_msg.edit_text("🧠 Пишу красивый конспект...")
            recent_notes = await get_recent_notes_for_linking(20)

            if is_document:
                stream_gen = stream_summarize_document(text, recent_notes=recent_notes)
                status_label = "Читаю документ и пишу конспект..."
            else:
                stream_gen = stream_process_examiner_text(text, recent_notes=recent_notes)
                status_label = "Нейросеть пишет конспект..."


            md_content = await stream_to_message(processing_msg, stream_gen, status_label)

            if not md_content:
                md_content = text

            date_str = datetime.datetime.now().strftime('%d.%m.%Y')
            doc_tags = "document, notes" if is_document else "notes"
            if not md_content.strip().startswith("---"):
                md_content = f"---\ntags: [{doc_tags}]\ndate: {date_str}\n---\n\n{md_content}"

            category = "Notes"
            s3_folder = f"TelegramBot/{category}"

            semantic_name = await generate_semantic_filename(md_content)
            md_filename = await get_rephrased_filename(category, semantic_name, text)

            await storage.upload_file(s3_folder, md_filename, md_content.encode('utf-8'))
            note_id = await add_note_log(md_filename, s3_folder, doc_tags, md_content)

            await safe_reply_with_note_actions(
                target=processing_msg,
                text=f"✅ Сохранен конспект в `{s3_folder}/{md_filename}`",
                filename=md_filename,
                category=s3_folder,
                note_id=note_id,
                is_edit=True
            )
        else:
            await processing_msg.edit_text("🧠 Думаю...")
            recent_notes = await get_recent_notes_for_linking(20)
            processed = await process_text(
                text,
                delay_callback=None,
                url_content=url_content,
                recent_notes=recent_notes
            )

            if not processed:
                await processing_msg.edit_text("❌ Ошибка при обработке заметки нейросетью.")
                return

            category = processed.get("category", "Notes")

            raw_tags = processed.get("tags", [])
            if isinstance(raw_tags, str):
                tags_list = [t.strip().lstrip('#') for t in raw_tags.split(",")]
            elif isinstance(raw_tags, list):
                tags_list = [str(t).strip().lstrip('#') for t in raw_tags]
            else:
                tags_list = []
            tags_str = ", ".join([t for t in tags_list if t])

            corrected_text = processed.get("corrected_text")
            if not corrected_text or not corrected_text.strip():
                corrected_text = text

            if category == "Reminders" and "**Связанные заметки:**" in corrected_text:
                corrected_text = corrected_text.split("**Связанные заметки:**")[0].strip()

            base_name = processed.get("filename", "").strip()
            if not base_name:
                base_name = f"заметка_{uuid.uuid4().hex[:4]}"

            md_filename = base_name if base_name.endswith('.md') else f"{base_name}.md"
            md_filename = await get_rephrased_filename(category, md_filename, text)

            reminder_time = processed.get("remind_time") or processed.get("reminder_time")
            if reminder_time and not str(reminder_time).strip():
                reminder_time = None

            try:
                recur_minutes = int(processed.get("recur_minutes", 0))
            except (ValueError, TypeError):
                recur_minutes = 0

            if not reminder_time and (category == "Reminders" or recur_minutes > 0):
                reminder_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

            date_str = datetime.datetime.now().strftime('%d.%m.%Y')
            md_content = f"---\ntags: [{tags_str}]\ndate: {date_str}\n---\n\n{corrected_text}"
            s3_folder = f"TelegramBot/{category}"

            await storage.upload_file(s3_folder, md_filename, md_content.encode('utf-8'))
            note_id = await add_note_log(md_filename, s3_folder, tags_str, md_content)

            # Expense tracking
            expense_amount = parse_expense_amount(processed.get("expense_amount"))

            if expense_amount > 0 and message.from_user:
                expense_comment = corrected_text.replace("\n", " ")[:200]
                await add_expense(
                    user_id=message.from_user.id,
                    amount=expense_amount,
                    comment=expense_comment
                )
                await append_monthly_expense(expense_amount, expense_comment)

            if reminder_time and category == "Reminders":
                try:
                    dt_obj = datetime.datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                    display_time = dt_obj.strftime("%d.%m.%Y %H:%M")
                except ValueError:
                    display_time = reminder_time

                await add_reminder(message.from_user.id, corrected_text, reminder_time, recur_minutes)
                recur_text = f" (повтор каждые {recur_minutes} мин)" if recur_minutes > 0 else ""
                await safe_reply_with_note_actions(
                    target=processing_msg,
                    text=f"⏰ Напоминание на {display_time}{recur_text}.\nФайл: `{s3_folder}/{md_filename}`",
                    filename=md_filename,
                    category=s3_folder,
                    note_id=note_id,
                    is_edit=True
                )
            else:
                expense_info = f"\n💰 Зафиксирован расход: **{expense_amount:,.2f}** руб." if expense_amount > 0 else ""
                await safe_reply_with_note_actions(
                    target=processing_msg,
                    text=f"✅ Сохранено в {s3_folder}: `{md_filename}`{expense_info}",
                    filename=md_filename,
                    category=s3_folder,
                    note_id=note_id,
                    is_edit=True
                )

    except Exception as e:
        logger.error(f"Ошибка при сохранении заметки: {e}")
        await processing_msg.edit_text(f"❌ Ошибка: {e}")
    finally:
        await state.clear()
        await message.answer("Готово! Вы вернулись в главное меню 🏠", reply_markup=get_main_keyboard())


@router.message(StateFilter(NoteState.waiting_for_text, None), F.document)
async def handle_document(message: Message, state: FSMContext, bot: Bot):
    if not message.document or not message.document.file_name:
        await message.answer("❌ Документ не содержит имени файла.")
        return

    doc_name = message.document.file_name
    ext = os.path.splitext(doc_name)[1].lower()
    supported_exts = {".docx", ".pdf", ".txt", ".md"}

    processing_msg = await message.answer(f"📄 Читаю документ `{doc_name}`...")

    if ext not in supported_exts:
        await processing_msg.edit_text("❌ Поддерживаются только форматы .docx, .pdf, .txt и .md.")
        if state is not None:
            await state.clear()
        return

    try:
        file_info = await bot.get_file(message.document.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        raw_bytes = downloaded_file.read()

        MAX_DOC_CHARS = 25000
        text = ""

        if ext == ".docx":
            doc = Document(io.BytesIO(raw_bytes))
            text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
        elif ext == ".pdf":
            try:
                import pymupdf as fitz
            except ImportError:
                import fitz
            text_pages = []
            with fitz.open(stream=raw_bytes, filetype="pdf") as pdf_doc:
                for page in pdf_doc:
                    p_text = page.get_text()
                    if p_text:
                        text_pages.append(p_text.strip())
            text = "\n\n".join(text_pages)
        elif ext in {".txt", ".md"}:
            try:
                text = raw_bytes.decode('utf-8')
            except UnicodeDecodeError:
                try:
                    text = raw_bytes.decode('cp1251')
                except Exception:
                    text = raw_bytes.decode('utf-8', errors='ignore')

        caption = getattr(message, "caption", None)
        if caption:
            text = f"{caption.strip()}\n\n{text}".strip()

        if len(text) > MAX_DOC_CHARS:
            text = text[:MAX_DOC_CHARS] + f"\n\n[Документ обрезан до {MAX_DOC_CHARS} символов во избежание превышения лимитов нейросети]"


        if not text or not text.strip():
            await processing_msg.edit_text("❌ Документ оказался пустым.")
            if state is not None:
                await state.clear()
            return

        await process_note_text(message, state, text.strip(), processing_msg, is_document=True)
    except Exception as e:
        logger.error(f"Ошибка при обработке документа: {e}")
        await processing_msg.edit_text(f"❌ Ошибка обработки документа: {e}")
        if state is not None:
            await state.clear()
        await message.answer("Готово! Вы вернулись в главное меню 🏠", reply_markup=get_main_keyboard())


@router.message(F.text.in_({"💰 Расходы", "Расходы"}))
@router.message(Command("expenses"))
async def cmd_expenses(message: Message, state: Optional[FSMContext] = None):
    """Displays expense summary for today and current month."""
    if state is not None:
        await state.clear()
    user_id = message.from_user.id if message.from_user else 0
    summary = await get_expenses_summary(user_id)

    now = datetime.datetime.now()
    today_str = now.strftime("%d.%m.%Y")
    month_name_str = now.strftime("%m.%Y")

    today_sum = summary["today_sum"]
    today_count = summary["today_count"]
    month_sum = summary["month_sum"]
    month_count = summary["month_count"]
    today_records = summary.get("today_records", [])

    lines = [
        "📊 **Учет расходов**\n",
        f"📅 **Сегодня ({today_str}):**",
        f"• Всего: **{today_sum:,.2f}** руб. ({today_count} записей)"
    ]

    if today_records:
        lines.append("\n_Последние расходы за сегодня:_")
        for amt, comment, dt in today_records[:5]:
            time_only = dt.split()[1][:5] if " " in str(dt) else ""
            clean_comm = (comment or "Расход").strip().replace("\n", " ")
            lines.append(f"• `{time_only}` — **{amt:,.2f}** руб. ({clean_comm})")

    lines.append(f"\n🗓 **Текущий месяц ({month_name_str}):**")
    lines.append(f"• Всего: **{month_sum:,.2f}** руб. ({month_count} записей)")

    if today_count == 0 and month_count == 0:
        lines.append("\n_Записей о расходах пока нет. Чтобы добавить расход, отправьте сообщение с суммой, например: «Потратил 450 руб на обед»._")

    full_text = "\n".join(lines)
    try:
        await message.answer(full_text, parse_mode="Markdown")
    except TelegramBadRequest:
        await message.answer(full_text.replace("**", "").replace("_", "").replace("`", ""))


@router.message(F.text.in_({"📅 Ежедневник", "Ежедневник"}))
@router.message(Command("daily"))
async def cmd_daily(message: Message, state: FSMContext):
    """Appends note/todo to today's daily note or prompts for text with format choices."""
    args = message.text.split(maxsplit=1) if message.text and message.text.startswith("/") else []
    if len(args) > 1:
        await state.clear()
        note_text = args[1].strip()
        filename, folder, note_id = await append_daily_note(note_text)
        now_time = datetime.datetime.now().strftime("%H:%M")
        msg_text = f"✅ Добавлено в ежедневник `{folder}/{filename}`:\n- {now_time}: {note_text}"
        await safe_reply_with_note_actions(
            target=message,
            text=msg_text,
            filename=filename,
            category=folder,
            note_id=note_id,
            is_edit=False
        )
    else:
        await state.clear()
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        await state.set_state(DailyState.waiting_for_text)
        await message.answer(
            f"📅 Введите запись или задачу для ежедневника на сегодня ({today}):\n(или выберите формат кнопками ниже)",
            parse_mode="Markdown",
            reply_markup=get_daily_format_keyboard()
        )



@router.callback_query(F.data.startswith("daily_fmt:"))
async def handle_daily_format_callback(callback: CallbackQuery, state: FSMContext):
    """Sets formatting prefix for daily note entry."""
    fmt_type = callback.data.split(":", 1)[1]
    if fmt_type == "todo":
        await state.update_data(daily_prefix="- [ ] ")
        prompt = "☑️ Введите задачу для ежедневника (будет оформлена как чекбокс `- [ ]`):"
    elif fmt_type == "idea":
        await state.update_data(daily_prefix="💡 ")
        prompt = "💡 Введите идею для ежедневника:"
    else:
        await state.update_data(daily_prefix="")
        prompt = "📝 Введите заметку или мысль для ежедневника:"
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(prompt)


@router.message(
    DailyState.waiting_for_text,
    F.text,
    ~F.text.in_(ALL_NAVIGATION_BUTTONS),
    ~F.text.func(is_bot_command)
)
async def handle_daily_input(message: Message, state: FSMContext):
    """Handles text input for daily note state with optional formatting prefix."""
    data = await state.get_data()
    prefix = data.get("daily_prefix", "")
    raw_text = message.text.strip()
    note_text = f"{prefix}{raw_text}" if prefix else raw_text

    filename, folder, note_id = await append_daily_note(note_text)
    now_time = datetime.datetime.now().strftime("%H:%M")
    await state.clear()
    msg_text = f"✅ Добавлено в ежедневник `{folder}/{filename}`:\n- {now_time}: {note_text}"
    await safe_reply_with_note_actions(
        target=message,
        text=msg_text,
        filename=filename,
        category=folder,
        note_id=note_id,
        is_edit=False
    )
    await message.answer("Готово! Вы вернулись в главное меню 🏠", reply_markup=get_main_keyboard())


@router.message(F.text.in_({"🏷️ Каталог тегов", "Каталог тегов"}))
@router.message(Command("tags"))
async def cmd_tags(message: Message, state: Optional[FSMContext] = None):
    """Lists top tags with counts and inline buttons."""
    if state is not None:
        await state.clear()
    tags_with_counts = await get_all_tags_with_counts()
    if not tags_with_counts:
        await message.answer(
            "🏷 В ваших заметках пока нет тегов.\n"
            "Добавляйте теги (например, `#работа`, `#финансы`) в заметки, и они появятся здесь."
        )
        return

    kb = get_tags_keyboard(tags_with_counts)
    await message.answer(
        "🏷 **Обзор тегов (Tag Explorer)**\n\n"
        "Выберите тег, чтобы увидеть связанные заметки:",
        reply_markup=kb,
        parse_mode="Markdown"
    )


@router.message(F.text.in_({"⚙️ Статус и справка", "Статус и справка"}))
@router.message(Command("status"))
@router.message(Command("help"))
async def cmd_status_and_help(message: Message, state: Optional[FSMContext] = None):
    """Displays system status, AI settings, and help diagnostics."""
    if state is not None:
        await state.clear()
    settings = get_settings()
    total_notes = await get_total_notes_count()
    today_notes = await get_today_notes()
    today_count = len(today_notes)

    proxy_info = "✅ Включен" if settings.PROXY_URL else "Отключен"
    s3_info = f"✅ Подключено (`{settings.S3_BUCKET_NAME}`)" if settings.has_s3 else "⚠️ Не настроено"
    audio_info = "✅ Whisper (Groq)" if settings.has_groq else "⚠️ Отключена (нет GROQ_API_KEY)"

    text = (
        "⚙️ **Статус системы и справка**\n\n"
        f"🤖 **ИИ-провайдер:** `{settings.active_ai_provider_display}`\n"
        f"🧠 **Модель:** `{settings.AI_MODEL}`\n"
        f"🌐 **Прокси:** {proxy_info}\n"
        f"📚 **Всего заметок в базе:** `{total_notes}`\n"
        f"📝 **Заметок за сегодня:** `{today_count}`\n"
        f"☁️ **S3 Хранилище:** {s3_info}\n"
        f"🎧 **Транскрибация аудио:** {audio_info}\n"
        f"⏱ **Часовой пояс:** `{settings.TIMEZONE}`\n\n"
        "📖 **Быстрые команды:**\n"
        "• `/daily` — ежедневник (задачи и мысли на день)\n"
        "• `/expenses` — учет и статистика расходов\n"
        "• `/tags` — каталог заметок по тегам\n"
        "• `/sync` — полная синхронизация S3\n"
        "• `/status` — статус бота и проверка связи\n\n"
        "Нажмите кнопку ниже для проверки связи с нейросетью или синхронизации:"
    )
    try:
        await message.answer(text, reply_markup=get_status_keyboard(), parse_mode="Markdown")
    except TelegramBadRequest:
        await message.answer(text.replace("**", "").replace("`", ""), reply_markup=get_status_keyboard())

cmd_help = cmd_status_and_help
cmd_status = cmd_status_and_help


@router.callback_query(F.data == "test_ai")
async def handle_test_ai_callback(callback: CallbackQuery):
    """Diagnostics test pinging the active AI provider."""
    await callback.answer("⏳ Проверяю связь с ИИ...")
    if callback.message:
        try:
            await callback.message.edit_text("⏳ Отправляю тестовый запрос к ИИ-провайдеру...")
        except Exception:
            pass

    success, msg, latency = await test_ai_connection()
    settings = get_settings()
    icon = "✅" if success else "❌"
    res_text = (
        f"{icon} **Результат проверки связи с ИИ:**\n\n"
        f"• **Провайдер:** `{settings.active_ai_provider_display}`\n"
        f"• **Модель:** `{settings.AI_MODEL}`\n"
        f"• **Задержка ответа:** `{latency} сек.`\n"
        f"• **Статус:** {msg}\n"
    )
    if callback.message:
        try:
            await callback.message.edit_text(res_text, reply_markup=get_status_keyboard(), parse_mode="Markdown")
        except TelegramBadRequest:
            await callback.message.edit_text(res_text.replace("**", "").replace("`", ""), reply_markup=get_status_keyboard())


@router.callback_query(F.data == "sync_now")
async def handle_sync_now_callback(callback: CallbackQuery):
    """Triggers S3 sync directly from the status keyboard."""
    await callback.answer("⏳ Запущена синхронизация...")
    if callback.message:
        try:
            await callback.message.edit_text("🔄 Синхронизирую базу данных с S3 хранилищем...")
        except Exception:
            pass
    try:
        sync_res = await run_s3_sync(return_deleted=True)
        added = sync_res[0]
        updated = sync_res[1]
        deleted = sync_res[2] if len(sync_res) > 2 else 0
        del_info = f"• Удалено устаревших: **{deleted}**\n" if deleted > 0 else ""
        res_text = (
            f"✅ **Синхронизация S3 завершена!**\n\n"
            f"• Добавлено заметок: **{added}**\n"
            f"• Обновлено заметок: **{updated}**\n"
            f"{del_info}\n"
            f"База данных и поиск актуальны."
        )
        if callback.message:
            try:
                await callback.message.edit_text(res_text, reply_markup=get_status_keyboard(), parse_mode="Markdown")
            except TelegramBadRequest:
                await callback.message.edit_text(res_text.replace("**", "").replace("`", ""), reply_markup=get_status_keyboard())
    except Exception as e:
        logger.error(f"Ошибка синхронизации по кнопке: {e}")
        if callback.message:
            await callback.message.edit_text(f"❌ Ошибка синхронизации: {e}", reply_markup=get_status_keyboard())



@router.callback_query(F.data.startswith("tag:"))
async def handle_tag_callback(callback: CallbackQuery):
    """Handles tag button click, displaying notes with that tag."""
    tag_name = callback.data.split(":", 1)[1]
    notes = await get_notes_by_tag(tag_name, limit=25)

    if not notes:
        await callback.answer(f"По тегу #{tag_name} заметок не найдено.", show_alert=True)
        return

    files_list = [r[0] for r in notes]
    categories_list = [r[1] for r in notes]

    response_text = f"🏷 Заметки с тегом **#{tag_name}** ({len(notes)}):\n\n"
    for i, (filename, category) in enumerate(notes, 1):
        clean_cat = category.replace("TelegramBot/", "") if category else "Корень"
        if not clean_cat or clean_cat == "Root":
            clean_cat = "Корень"
        response_text += f"{i}. `[{clean_cat}]` {filename}\n"
    response_text += "\nНажмите номер файла ниже для скачивания:"

    inline_kb = get_search_results_inline_kb(len(files_list))

    msg = getattr(callback, "message", None)
    chat = getattr(msg, "chat", None)
    chat_id = getattr(chat, "id", None)
    msg_id = getattr(msg, "message_id", None)
    if chat_id is not None and msg_id is not None:
        search_results_cache.set(chat_id, msg_id, files_list, categories_list)

    try:
        await callback.message.edit_text(response_text, reply_markup=inline_kb, parse_mode="Markdown")
    except TelegramBadRequest:
        await callback.message.edit_text(response_text, reply_markup=inline_kb)
    await callback.answer()


@router.callback_query(F.data.startswith("dl_note:"))
async def handle_dl_note_callback(callback: CallbackQuery):
    """Handles callback to download a saved note directly as a .md file."""
    try:
        note_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный идентификатор заметки.", show_alert=True)
        return

    note = await get_note_by_id(note_id)
    if not note:
        await callback.answer("❌ Заметка не найдена в базе данных.", show_alert=True)
        return

    filename = note["filename"]
    category = note["category"]

    content = await storage.download_file(category, filename)
    if not content and note.get("content"):
        content = note["content"].encode('utf-8')

    if content:
        if callback.message:
            await callback.message.answer_document(
                BufferedInputFile(content, filename=filename),
                reply_markup=get_main_keyboard()
            )
        await callback.answer("📥 Файл отправлен")
    else:
        await callback.answer("❌ Не удалось получить содержимое файла.", show_alert=True)


@router.callback_query(F.data.startswith("del_note:"))
async def handle_del_note_callback(callback: CallbackQuery):
    """Handles callback asking for confirmation before deleting a note."""
    try:
        note_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный идентификатор заметки.", show_alert=True)
        return

    note = await get_note_by_id(note_id)
    if not note:
        await callback.answer("❌ Заметка не найдена.", show_alert=True)
        return

    fname = note["filename"]
    confirm_kb = get_delete_confirm_keyboard(note_id)
    try:
        await callback.message.edit_text(
            f"⚠️ Вы уверены, что хотите удалить заметку `{fname}` из базы данных и S3 хранилища?",
            reply_markup=confirm_kb,
            parse_mode="Markdown"
        )
    except TelegramBadRequest:
        await callback.message.edit_text(
            f"⚠️ Вы уверены, что хотите удалить заметку {fname} из базы данных и S3 хранилища?",
            reply_markup=confirm_kb
        )
    await callback.answer()


@router.callback_query(F.data.startswith("del_confirm:"))
async def handle_del_confirm_callback(callback: CallbackQuery):
    """Deletes note from DB and S3 after confirmation."""
    try:
        note_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка идентификатора заметки.", show_alert=True)
        return

    note = await get_note_by_id(note_id)
    if not note:
        await callback.message.edit_text("❌ Заметка уже была удалена ранее.")
        await callback.answer()
        return

    fname = note["filename"]
    category = note["category"]

    # Delete from DB
    await delete_notes([(fname, category)])
    # Delete from S3
    await storage.delete_file(category, fname)

    try:
        await callback.message.edit_text(
            f"🗑 Заметка `{fname}` успешно удалена из базы и S3.",
            parse_mode="Markdown"
        )
    except TelegramBadRequest:
        await callback.message.edit_text(f"🗑 Заметка {fname} успешно удалена из базы и S3.")
    await callback.answer("Заметка удалена")


@router.callback_query(F.data.startswith("del_cancel:"))
async def handle_del_cancel_callback(callback: CallbackQuery):
    """Cancels note deletion, restoring the action keyboard."""
    try:
        note_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer()
        return

    note = await get_note_by_id(note_id)
    if not note:
        await callback.message.edit_text("Действие отменено.")
        await callback.answer()
        return

    fname = note["filename"]
    cat = note["category"]
    await safe_reply_with_note_actions(
        target=callback.message,
        text=f"✅ Заметка сохранена в {cat}: `{fname}`",
        filename=fname,
        category=cat,
        note_id=note_id,
        is_edit=True
    )
    await callback.answer("Удаление отменено")



async def run_s3_sync(return_deleted: bool = False) -> Union[Tuple[int, int], Tuple[int, int, int]]:
    """
    Synchronizes S3 objects into local database using delta sync (ETag & LastModified).
    Only downloads new or modified files. Prunes files deleted in S3 from local database.
    Commits database changes in a single batch transaction.
    Returns: (added_count, updated_count) or (added_count, updated_count, deleted_count)
    """
    settings = get_settings()

    s3_items = await storage.list_files_with_metadata()
    if not s3_items and hasattr(storage, "get_all_files"):
        try:
            raw_files = await storage.get_all_files()
            if raw_files:
                s3_items = [{"key": f, "etag": "", "last_modified": "", "size": 0} for f in raw_files]
        except Exception:
            pass

    md_objects = [
        item for item in s3_items
        if (item.get("key", "").endswith(".md") if isinstance(item, dict) else str(item).endswith(".md"))
        and ".obsidian/" not in (item.get("key", "") if isinstance(item, dict) else str(item))
        and ".trash/" not in (item.get("key", "") if isinstance(item, dict) else str(item))
    ]

    s3_map: Dict[str, Dict[str, Any]] = {}
    for item in md_objects:
        if isinstance(item, dict):
            k = item.get("key", "")
            s3_map[k] = item
        else:
            k = str(item)
            s3_map[k] = {"key": k, "etag": "", "last_modified": "", "size": 0}

    # If S3 has no markdown files (e.g. empty or unconfigured), don't wipe local DB
    if not s3_map:
        return (0, 0, 0) if return_deleted else (0, 0)

    # Fetch current metadata from SQLite
    db_meta_map = await get_all_notes_metadata()
    # Map (category, filename) -> key
    db_keys_map: Dict[str, Dict[str, Any]] = {}
    for (cat, fname), meta in db_meta_map.items():
        k = f"{cat}/{fname}" if cat and cat != "Root" else fname
        db_keys_map[k] = meta

    # 1. Cleanup deleted files: notes in SQLite that no longer exist in S3
    keys_to_delete = [k for k in db_keys_map if k not in s3_map]
    deleted_count = 0
    if keys_to_delete:
        pairs_to_delete = []
        for k in keys_to_delete:
            meta = db_keys_map[k]
            pairs_to_delete.append((meta["filename"], meta["category"]))
        deleted_count = await delete_notes(pairs_to_delete, commit=False)
        logger.info(f"S3 sync pruned {deleted_count} deleted notes from database.")

    # 2. Determine which files need downloading (delta sync)
    keys_to_download: List[str] = []
    for key, item in s3_map.items():
        if key not in db_keys_map:
            # Brand new note
            keys_to_download.append(key)
        else:
            db_meta = db_keys_map[key]
            s3_etag = item.get("etag", "")
            s3_lm = item.get("last_modified", "")
            db_etag = db_meta.get("etag") or ""
            db_lm = db_meta.get("last_modified") or ""

            # If DB doesn't have metadata recorded yet, download to sync
            if not db_etag and not db_lm:
                keys_to_download.append(key)
            elif (s3_etag and db_etag and s3_etag != db_etag):
                keys_to_download.append(key)
            elif (s3_lm and db_lm and str(s3_lm) != str(db_lm)):
                keys_to_download.append(key)
            elif (s3_etag != db_etag) or (s3_lm != db_lm):
                keys_to_download.append(key)

    # 3. Download only changed / new files
    downloaded = await storage.batch_download(
        keys_to_download,
        max_concurrency=settings.S3_SYNC_CONCURRENCY
    ) if keys_to_download else {}

    added_count = 0
    updated_count = 0

    for key, content_bytes in downloaded.items():
        if not content_bytes:
            continue

        parts = key.split('/')
        filename = parts[-1]
        category = "" if len(parts) == 1 else "/".join(parts[:-1])
        text_content = content_bytes.decode('utf-8', errors='ignore')

        item_meta = s3_map.get(key, {})
        etag = item_meta.get("etag", "")
        last_modified = item_meta.get("last_modified", "")

        try:
            is_added, is_updated = await upsert_note_log(
                filename=filename,
                category=category,
                tags="",
                content=text_content,
                etag=etag,
                last_modified=last_modified,
                commit=False
            )
            if is_added:
                added_count += 1
            elif is_updated:
                updated_count += 1
        except Exception as e:
            logger.warning(f"Ошибка сохранения файла {key} в БД: {e}")

    # 4. Batch DB transaction commit
    await commit_db()

    if return_deleted:
        return added_count, updated_count, deleted_count
    return added_count, updated_count


@router.message(Command("sync"))
@router.message(F.text.in_({"🔄 Синхронизация S3", "🔄 Полная синхронизация S3"}))
async def cmd_sync_s3(message: Message, state: Optional[FSMContext] = None):
    if state is not None:
        await state.clear()
    msg = await message.answer("🔄 Запущена глубокая синхронизация базы данных с SeaweedFS...")
    try:
        sync_res = await run_s3_sync(return_deleted=True)
        added = sync_res[0]
        updated = sync_res[1]
        deleted = sync_res[2] if len(sync_res) > 2 else 0

        del_info = f"Удалено устаревших заметок: **{deleted}**\n" if deleted > 0 else ""
        await msg.edit_text(
            f"✅ Синхронизация с SeaweedFS завершена!\n\n"
            f"Добавлено заметок: **{added}**\n"
            f"Обновлено заметок: **{updated}**\n"
            f"{del_info}\n"
            f"Теперь локальный ИИ и поиск полностью актуальны.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка ручной синхронизации: {e}")
        await msg.edit_text(f"❌ Ошибка во время синхронизации: {e}")