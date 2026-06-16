from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.services.llm import get_rephrased_filename, process_text, summarize_document, process_examiner_text, transcribe_audio
from app.services.s3_storage import S3StorageService
from app.database.db import add_reminder, add_note_log
from app.keyboards.reply import get_cancel_keyboard, get_main_keyboard
from docx import Document
from aiogram.fsm.state import State, StatesGroup
import os
import datetime
import uuid
import io
import aiosqlite


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
    await message.answer("Напиши текст, скинь ссылку, документ .docx, голосовое сообщение или фото:", reply_markup=get_cancel_keyboard())
    await state.set_state(NoteState.waiting_for_text)

@router.message(NoteState.waiting_for_text, F.photo)
async def handle_photo(message: Message, state: FSMContext, bot):
    processing_msg = await message.answer("Сохраняю фото...")
    
    async def on_delay():
        try:
            await processing_msg.edit_text("⚠️ Извините за задержку, пробую достучаться еще раз...")
        except Exception:
            pass

    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    downloaded_file = await bot.download_file(file_info.file_path)
    
    filename_img = f"img_{uuid.uuid4().hex[:8]}.jpg"
    s3_img_folder = "TelegramBot/Attachments"
    await storage.upload_file(s3_img_folder, filename_img, downloaded_file.read())
    
    caption = message.caption if message.caption else ""
    text_to_process = f"{md_content}\n{caption}"
    
    md_content = f"![[{filename_img}]]\n"
    category = "Notes"
    base_name = processed.get("filename", "").strip()
    if not base_name:
        base_name = f"photo_{uuid.uuid4().hex[:4]}"
    md_filename = base_name if base_name.endswith('.md') else f"{base_name}.md"
    timestamp = datetime.datetime.now().strftime("%H%M%S")
    tags_str = ""
    corrected_text = ""
    
    if message.caption:
        processed = await process_text(message.caption, delay_callback=on_delay)
        category = processed.get("category", "Notes")
        tags = processed.get("tags", [])
        tags_str = ", ".join(tags)
        corrected_text = processed.get("corrected_text", "")
        
        md_content = f"---\ntags: [{tags_str}]\ndate: {datetime.datetime.now().strftime('%Y-%m-%d')}\n---\n\n{md_content}\n{corrected_text}"
        base_name = processed.get("filename", "photo")
        md_filename = f"{base_name}.md"
        
        reminder_time = processed.get("reminder_time")
        if reminder_time and category == "Reminders":
            await add_reminder(message.from_user.id, corrected_text, reminder_time)

    await storage.upload_file(f"TelegramBot/{category}", md_filename, md_content.encode('utf-8'))
    await add_note_log(md_filename, category, tags_str, corrected_text)
    
    await processing_msg.edit_text(f"Фото ({md_filename}) успешно сохранено в {category}.")
    await state.clear()
    await message.answer("Вы в главном меню.", reply_markup=get_main_keyboard())

@router.message(NoteState.waiting_for_text, F.voice | F.audio)
async def handle_audio(message: Message, state: FSMContext, bot):
    processing_msg = await message.answer("🎧 Распознаю аудио...")
    file_id = message.voice.file_id if message.voice else message.audio.file_id
    file_info = await bot.get_file(file_id)
    downloaded_file = await bot.download_file(file_info.file_path)
    
    temp_filename = f"temp_{uuid.uuid4().hex[:8]}.ogg"
    with open(temp_filename, "wb") as f:
        f.write(downloaded_file.read())
        
    text = await transcribe_audio(temp_filename)
    os.remove(temp_filename)
    
    if not text:
        await processing_msg.edit_text("Не удалось распознать аудио.")
        return
        
    await process_note_text(message, state, text, processing_msg)
    
@router.message(NoteState.waiting_for_text, F.text)
async def handle_text(message: Message, state: FSMContext):
    processing_msg = await message.answer("🧠 Анализирую текст...")
    await process_note_text(message, state, message.text, processing_msg)

async def process_note_text(message: Message, state: FSMContext, text: str, processing_msg: Message):
    if len(text) > 1000:
        result_text = await process_examiner_text(text)
        
        md_content = result_text
        category = "Notes"
        s3_folder = f"TelegramBot/{category}"
        
        raw_filename = f"note_{uuid.uuid4().hex[:4]}.md"
        md_filename = await get_rephrased_filename(category, raw_filename, text)
        
        await storage.upload_file(s3_folder, md_filename, md_content.encode('utf-8'))
        await add_note_log(md_filename, s3_folder, "notes", md_content)
        await processing_msg.edit_text(f"Сохранен конспект в {s3_folder}: `{md_filename}`", parse_mode="Markdown")
    else:
        processed = await process_text(text)
        
        if not processed:
            await processing_msg.edit_text("❌ Ошибка при обработке заметки.")
            await state.clear()
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

        base_name = processed.get("filename", "").strip()
        if not base_name:
            base_name = f"заметка_{uuid.uuid4().hex[:4]}"
            
        md_filename = base_name if base_name.endswith('.md') else f"{base_name}.md"
        md_filename = md_filename.replace(" ", "_")
        
        md_filename = await get_rephrased_filename(category, md_filename, text)
        
        reminder_time = processed.get("remind_time") or processed.get("reminder_time")
        if reminder_time and not str(reminder_time).strip():
            reminder_time = None
        
        date_str = datetime.datetime.now().strftime('%d.%m.%Y')
        md_content = f"---\ntags: [{tags_str}]\ndate: {date_str}\n---\n\n{corrected_text}"
        s3_folder = f"TelegramBot/{category}"
        
        await storage.upload_file(s3_folder, md_filename, md_content.encode('utf-8'))
        await add_note_log(md_filename, s3_folder, tags_str, corrected_text)
        
        if reminder_time and category == "Reminders":
            try:
                dt_obj = datetime.datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                display_time = dt_obj.strftime("%d.%m.%Y %H:%M")
            except:
                display_time = reminder_time
                
            await add_reminder(message.from_user.id, corrected_text, reminder_time)
            await processing_msg.edit_text(f"Напоминание на {display_time}. Файл: `{s3_folder}/{md_filename}`", parse_mode="Markdown")
        else:
            await processing_msg.edit_text(f"Сохранено в {s3_folder}: `{md_filename}`", parse_mode="Markdown")
            
    await state.clear()
    await message.answer("Готово! Вы вернулись в главное меню 🏠", reply_markup=get_main_keyboard())
    
@router.message(NoteState.waiting_for_text, F.document)
async def handle_document(message: Message, state: FSMContext, bot):
    processing_msg = await message.answer("📄 Читаю документ...")
    
    if not message.document.file_name.endswith('.docx'):
        await processing_msg.edit_text("❌ Пожалуйста, отправьте файл формата .docx")
        return
        
    file_info = await bot.get_file(message.document.file_id)
    downloaded_file = await bot.download_file(file_info.file_path)
    
    try:
        doc = Document(io.BytesIO(downloaded_file.read()))
        text = "\n".join([para.text for para in doc.paragraphs])
    except Exception as e:
        await processing_msg.edit_text("❌ Ошибка при чтении файла.")
        return

    if text:
        await processing_msg.edit_text("🧠 Генерирую конспект...")
        
        processed = await summarize_document(text, message.document.file_name) 
        
        if processed:
            summary = processed.get("summary") or processed.get("markdown_content")
            if not summary or not summary.strip():
                summary = f"### Оригинальный текст документа (ИИ вернул пустой ответ):\n\n{text}"

            tags = processed.get("tags", [])
            
            if isinstance(tags, str):
                tags_list = [t.strip().lstrip('#') for t in tags.split(",")]
            elif isinstance(tags, list):
                tags_list = [str(t).strip().lstrip('#') for t in tags]
            else:
                tags_list = []
                
            tags_str = ", ".join([t for t in tags_list if t])
            
            date_str = datetime.datetime.now().strftime('%d.%m.%Y')
            full_text = f"---\ntags: [{tags_str}]\ndate: {date_str}\n---\n\n{summary}"
            
            category = "Notes"
            s3_folder = f"TelegramBot/{category}"

            clean_name = processed.get("filename", "").strip()
            if not clean_name:
                clean_name = f"документ_{uuid.uuid4().hex[:4]}.md"
                
            fname = clean_name if clean_name.endswith('.md') else f"{clean_name}.md" 
            fname = fname.replace(" ", "_")
        
            fname = await get_rephrased_filename(category, fname, summary)

            # Загружаем в S3
            await storage.upload_file(s3_folder, fname, full_text.encode('utf-8'))
            
            await add_note_log(fname, s3_folder, tags_str, summary)
            await processing_msg.edit_text(f"✅ Документ законспектирован в `{s3_folder}/{fname}`", parse_mode="Markdown")
        else:
            await processing_msg.edit_text("❌ Ошибка при генерации саммари.")
    else:
        await processing_msg.edit_text("❌ Документ оказался пустым.")
        
    await state.clear()
    await message.answer("Готово! Вы вернулись в главное меню 🏠", reply_markup=get_main_keyboard())
    
async def run_s3_sync():
    async with aiosqlite.connect("database.db") as db:
        files = await storage.get_all_files()
        added_count = 0
        updated_count = 0

        for key in files:
            if not key.endswith('.md') or '.obsidian/' in key or '.trash/' in key:
                continue
            
            parts = key.split('/')
            filename = parts[-1]
            category = "Root" if len(parts) == 1 else "/".join(parts[:-1])

            try:
                content_bytes = await storage.download_file_by_key(key)
                if not content_bytes:
                    continue
                    
                text_content = content_bytes.decode('utf-8', errors='ignore')
                
                async with db.execute(
                    "SELECT id, content FROM notes_log WHERE filename = ? AND category = ?", 
                    (filename, category)
                ) as cursor:
                    exists = await cursor.fetchone()
                
                if not exists:
                    await db.execute(
                        "INSERT INTO notes_log (filename, category, tags, content, date) VALUES (?, ?, ?, ?, ?)",
                        (filename, category, "", text_content, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    )
                    added_count += 1
                else:
                    if exists[1] != text_content:
                        await db.execute(
                            "UPDATE notes_log SET content = ? WHERE id = ?",
                            (text_content, exists[0])
                        )
                        updated_count += 1
                        
            except Exception as e:
                print(f"⚠️ Ошибка синхронизации файла {key}: {e}")
                continue
                
        await db.commit()
        
    return added_count, updated_count

@router.message(Command("sync"))
@router.message(F.text == "🔄 Полная синхронизация S3")
async def cmd_sync_s3(message: Message):
    msg = await message.answer("🔄 Запущена глубокая синхронизация базы данных с SeaweedFS...")
    
    added, updated = await run_s3_sync()
        
    await msg.edit_text(
        f"✅ Синхронизация с SeaweedFS завершена!\n\n"
        f"Добавлено заметок: **{added}**\n"
        f"Обновлено заметок: **{updated}**\n\n"
        f"Теперь локальный ИИ и поиск полностью актуальны.",
        parse_mode="Markdown"
    )