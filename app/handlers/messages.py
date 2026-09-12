import os
import io
import time
import uuid
import datetime
import asyncio
from typing import Tuple

from aiogram import Router, F, Bot
from aiogram.types import Message
from aiogram.filters import Command
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
    generate_semantic_filename
)
from app.services.s3_storage import S3StorageService
from app.services.scraper import extract_urls, fetch_url_content
from app.database.db import (
    add_reminder,
    add_note_log,
    upsert_note_log,
    get_recent_notes_for_linking
)
from app.keyboards.reply import get_cancel_keyboard, get_main_keyboard
from app.utils.streaming import stream_to_message
from logger import logger

router = Router()
storage = S3StorageService()

class NoteState(StatesGroup):
    waiting_for_text = State()


@router.message(F.text == "🔙 В главное меню")
async def cmd_back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Вы вернулись в главное меню.", reply_markup=get_main_keyboard())


@router.message(F.text == "📝 Отправить заметку")
async def prompt_note(message: Message, state: FSMContext):
    await message.answer(
        "Напиши текст, скинь ссылку, документ .docx, голосовое сообщение или фото:", 
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(NoteState.waiting_for_text)


@router.message(NoteState.waiting_for_text, F.photo)
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
            tags = processed.get("tags", [])
            tags_str = ", ".join(tags) if isinstance(tags, list) else str(tags)

            if processed.get("corrected_text"):
                corrected_text = f"{md_content}\n{processed.get('corrected_text')}"

            if processed.get("filename"):
                base_name = processed.get("filename").strip()

        md_filename = base_name if base_name.endswith('.md') else f"{base_name}.md"
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d")

        final_md = f"---\ntags: [{tags_str}]\ndate: {timestamp}\n---\n\n{corrected_text}"

        await add_note_log(filename=md_filename, category=category, tags=tags_str, content=final_md)
        await processing_msg.edit_text(f"✅ Фото сохранено как `{md_filename}` в категорию `{category}`")
    except Exception as e:
        logger.error(f"Ошибка при обработке фото: {e}")
        await processing_msg.edit_text(f"❌ Произошла ошибка при сохранении фото: {e}")
    finally:
        await state.clear()
        await message.answer("Готово! Вы вернулись в главное меню 🏠", reply_markup=get_main_keyboard())


@router.message(NoteState.waiting_for_text, F.voice | F.audio)
async def handle_audio(message: Message, state: FSMContext, bot: Bot):
    processing_msg = await message.answer("🎧 Распознаю аудио...")
    file_id = message.voice.file_id if message.voice else message.audio.file_id
    file_info = await bot.get_file(file_id)
    downloaded_file = await bot.download_file(file_info.file_path)

    temp_filename = f"temp_{uuid.uuid4().hex[:8]}.ogg"
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


@router.message(NoteState.waiting_for_text, F.text)
async def handle_text(message: Message, state: FSMContext):
    urls = extract_urls(message.text)
    url_content = ""

    if urls:
        processing_msg = await message.answer(f"🌐 Загружаю содержимое ссылки: {urls[0]}...")
        url_content = await fetch_url_content(urls[0])
        if url_content:
            await processing_msg.edit_text("🧠 Анализирую веб-страницу и текст заметки...")
        else:
            await processing_msg.edit_text("🧠 Анализирую текст...")
    else:
        processing_msg = await message.answer("🧠 Анализирую текст...")

    await process_note_text(message, state, message.text, processing_msg, url_content=url_content)


async def process_note_text(
    message: Message,
    state: FSMContext,
    text: str,
    processing_msg: Message,
    url_content: str = ""
):
    try:
        if len(text) > 1000:
            await processing_msg.edit_text("🧠 Пишу красивый конспект...")
            recent_notes = await get_recent_notes_for_linking(20)

            stream_gen = stream_process_examiner_text(text, recent_notes=recent_notes)
            md_content = await stream_to_message(processing_msg, stream_gen, "Нейросеть пишет конспект...")

            if not md_content:
                md_content = text

            category = "Notes"
            s3_folder = f"TelegramBot/{category}"

            semantic_name = await generate_semantic_filename(md_content)
            md_filename = await get_rephrased_filename(category, semantic_name, text)

            await storage.upload_file(s3_folder, md_filename, md_content.encode('utf-8'))
            await add_note_log(md_filename, s3_folder, "notes", md_content)

            await processing_msg.edit_text(
                f"✅ Сохранен конспект в `{s3_folder}/{md_filename}`",
                parse_mode="Markdown"
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
            await add_note_log(md_filename, s3_folder, tags_str, corrected_text)

            if reminder_time and category == "Reminders":
                try:
                    dt_obj = datetime.datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                    display_time = dt_obj.strftime("%d.%m.%Y %H:%M")
                except ValueError:
                    display_time = reminder_time

                await add_reminder(message.from_user.id, corrected_text, reminder_time, recur_minutes)
                recur_text = f" (повтор каждые {recur_minutes} мин)" if recur_minutes > 0 else ""
                await processing_msg.edit_text(
                    f"⏰ Напоминание на {display_time}{recur_text}.\nФайл: `{s3_folder}/{md_filename}`",
                    parse_mode="Markdown"
                )
            else:
                await processing_msg.edit_text(
                    f"✅ Сохранено в {s3_folder}: `{md_filename}`",
                    parse_mode="Markdown"
                )

    except Exception as e:
        logger.error(f"Ошибка при сохранении заметки: {e}")
        await processing_msg.edit_text(f"❌ Ошибка: {e}")
    finally:
        await state.clear()
        await message.answer("Готово! Вы вернулись в главное меню 🏠", reply_markup=get_main_keyboard())


@router.message(NoteState.waiting_for_text, F.document)
async def handle_document(message: Message, state: FSMContext, bot: Bot):
    processing_msg = await message.answer("📄 Читаю документ...")

    if not message.document.file_name or not message.document.file_name.endswith('.docx'):
        await processing_msg.edit_text("❌ Пожалуйста, отправьте файл формата .docx")
        await state.clear()
        return

    try:
        file_info = await bot.get_file(message.document.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)

        doc = Document(io.BytesIO(downloaded_file.read()))
        text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])

        if not text:
            await processing_msg.edit_text("❌ Документ оказался пустым.")
            return

        await processing_msg.edit_text("🧠 Генерирую конспект...")
        recent_notes = await get_recent_notes_for_linking(20)

        stream_gen = stream_summarize_document(text, recent_notes=recent_notes)
        summary = await stream_to_message(processing_msg, stream_gen, "Читаю документ и пишу конспект...")

        if not summary:
            summary = f"### Оригинальный текст документа:\n\n{text}"

        tags_str = "конспект, документ"
        date_str = datetime.datetime.now().strftime('%d.%m.%Y')
        final_md = f"---\ntags: [{tags_str}]\ndate: {date_str}\n---\n\n{summary}"

        category = "Notes"
        s3_folder = f"TelegramBot/{category}"

        semantic_name = await generate_semantic_filename(summary)
        fname = await get_rephrased_filename(category, semantic_name, summary)

        await storage.upload_file(s3_folder, fname, final_md.encode('utf-8'))
        await add_note_log(fname, s3_folder, tags_str, summary)

        await processing_msg.edit_text(
            f"✅ Сохранен конспект в `{s3_folder}/{fname}`", 
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка при обработке документа: {e}")
        await processing_msg.edit_text(f"❌ Ошибка обработки документа: {e}")
    finally:
        await state.clear()
        await message.answer("Готово! Вы вернулись в главное меню 🏠", reply_markup=get_main_keyboard())


async def run_s3_sync() -> Tuple[int, int]:
    """
    Synchronizes S3 objects into local database using concurrent downloads.
    Returns: (added_count, updated_count)
    """
    settings = get_settings()
    files = await storage.get_all_files()
    md_keys = [
        key for key in files
        if key.endswith('.md') and '.obsidian/' not in key and '.trash/' not in key
    ]

    if not md_keys:
        return 0, 0

    downloaded = await storage.batch_download(md_keys, max_concurrency=settings.S3_SYNC_CONCURRENCY)

    added_count = 0
    updated_count = 0

    for key, content_bytes in downloaded.items():
        if not content_bytes:
            continue

        parts = key.split('/')
        filename = parts[-1]
        category = "Root" if len(parts) == 1 else "/".join(parts[:-1])
        text_content = content_bytes.decode('utf-8', errors='ignore')

        try:
            is_added, is_updated = await upsert_note_log(
                filename=filename,
                category=category,
                tags="",
                content=text_content
            )
            if is_added:
                added_count += 1
            elif is_updated:
                updated_count += 1
        except Exception as e:
            logger.warning(f"Ошибка сохранения файла {key} в БД: {e}")

    return added_count, updated_count


@router.message(Command("sync"))
@router.message(F.text == "🔄 Полная синхронизация S3")
async def cmd_sync_s3(message: Message):
    msg = await message.answer("🔄 Запущена глубокая синхронизация базы данных с SeaweedFS...")
    try:
        added, updated = await run_s3_sync()
        await msg.edit_text(
            f"✅ Синхронизация с SeaweedFS завершена!\n\n"
            f"Добавлено заметок: **{added}**\n"
            f"Обновлено заметок: **{updated}**\n\n"
            f"Теперь локальный ИИ и поиск полностью актуальны.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка ручной синхронизации: {e}")
        await msg.edit_text(f"❌ Ошибка во время синхронизации: {e}")