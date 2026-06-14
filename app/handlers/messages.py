from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.services.llm import process_text, summarize_document
from app.services.s3_storage import S3StorageService
from app.database.db import add_reminder, add_note_log
from app.keyboards.reply import get_cancel_keyboard, get_main_keyboard
from docx import Document
from aiogram.fsm.state import State, StatesGroup
from app.services.llm import process_examiner_text, transcribe_audio
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
    await storage.upload_file("Attachments", filename_img, downloaded_file.read())
    
    md_content = f"![[{filename_img}]]\n"
    category = "Notes"
    base_name = processed.get("filename", "photo")
    timestamp = datetime.datetime.now().strftime("%H%M%S")
    md_filename = f"{base_name}.md" 
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

    await storage.upload_file(category, md_filename, md_content.encode('utf-8'))
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
    
@router.message(NoteState.waiting_for_text, F.document)
async def handle_document(message: Message, state: FSMContext, bot):
    processing_msg = await message.answer("📄 Читаю документ...")
    if not message.document.file_name.endswith('.docx'):
        await processing_msg.edit_text("Пожалуйста, отправьте файл формата .docx")
        return
        
    file_info = await bot.get_file(message.document.file_id)
    downloaded_file = await bot.download_file(file_info.file_path)
    
    doc = Document(io.BytesIO(downloaded_file.read()))
    text = "\n".join([para.text for para in doc.paragraphs])
    
    await process_note_text(message, state, text, processing_msg)

@router.message(NoteState.waiting_for_text, F.text)
async def handle_text(message: Message, state: FSMContext):
    processing_msg = await message.answer("🧠 Анализирую текст...")
    await process_note_text(message, state, message.text, processing_msg)

async def process_note_text(message: Message, state: FSMContext, text: str, processing_msg: Message):
    if len(text) > 1000:
        result = await process_examiner_text(text)
        
        if not result:
            await processing_msg.edit_text("❌ Ошибка при генерации конспекта. Проверь логи (скорее всего, проблема с API или ключом).")
            await state.clear()
            return
            
        md_content = result.get("markdown_content", text)
        filename = f"{result.get('filename', 'exam_notes')}.md"
        category = "Notes"
        
        await storage.upload_file(category, filename, md_content.encode('utf-8'))
        await add_note_log(filename, category, "exam, notes", md_content)
        await processing_msg.edit_text(f"Сохранен конспект: `{filename}`", parse_mode="Markdown")
    else:
        processed = await process_text(text)
        
        if not processed:
            await processing_msg.edit_text("❌ Ошибка при обработке заметки.")
            await state.clear()
            return
            
        category = processed.get("category", "Notes")
        tags = processed.get("tags", [])
        tags_str = ", ".join(tags)
        corrected_text = processed.get("corrected_text", text)
        base_name = processed.get("filename", f"note_{uuid.uuid4().hex[:4]}")
        md_filename = f"{base_name}.md"
        reminder_time = processed.get("reminder_time")
        
        md_content = f"---\ntags: [{tags_str}]\ndate: {datetime.datetime.now().strftime('%Y-%m-%d')}\n---\n\n{corrected_text}"
        await storage.upload_file(category, md_filename, md_content.encode('utf-8'))
        await add_note_log(md_filename, category, tags_str, corrected_text)
        
        if reminder_time and category == "Reminders":
            await add_reminder(message.from_user.id, corrected_text, reminder_time)
            await processing_msg.edit_text(f"Напоминание на {reminder_time}. Файл: `{md_filename}`", parse_mode="Markdown")
        else:
            await processing_msg.edit_text(f"Сохранено в {category}: `{md_filename}`", parse_mode="Markdown")
            
    await state.clear()
    await message.answer("Готово.", reply_markup=get_main_keyboard())
    
@router.message(NoteState.waiting_for_text, F.document)
async def handle_document(message: Message, state: FSMContext):
    processing_msg = await message.answer("⏳ Читаю документ и составляю конспект, подожди немного...")
    
    doc = message.document
    file_info = await message.bot.get_file(doc.file_id)
    downloaded_file = await message.bot.download_file(file_info.file_path)
    content = downloaded_file.read()
    
    await storage.upload_file("Attachments", doc.file_name, content)
    
    text = ""
    if doc.file_name.endswith('.pdf'):
        import fitz
        with fitz.open(stream=content, filetype="pdf") as pdf:
            text = "".join(page.get_text() for page in pdf)
    elif doc.file_name.endswith('.docx'):
        from docx import Document as DocxReader
        import io
        docx_doc = DocxReader(io.BytesIO(content))
        text = "\n".join(p.text for p in docx_doc.paragraphs)

    if text:
        processed = await summarize_document(text)
        if processed:
            summary = processed.get("summary", "Конспект пуст")
            tags = ", ".join(processed.get("tags", ["document"]))
            full_text = f"---\ntags: [{tags}]\ndate: {datetime.datetime.now().strftime('%Y-%m-%d')}\n---\n\n{summary}\n\n**Оригинал:** [[{doc.file_name}]]"
            
            clean_name = processed.get("filename", "doc_summary")
            fname = f"{clean_name}.md" 
        
            await storage.upload_file("Notes", fname, full_text.encode('utf-8'))
            await add_note_log(fname, "Notes", tags, summary)
            
            await processing_msg.edit_text(f"✅ Документ изучен и сохранен в `Notes/{fname}`")
        else:
            await processing_msg.edit_text("❌ Не удалось проанализировать текст документа.")
    else:
        await processing_msg.edit_text("❌ Не удалось извлечь текст из файла.")

    await state.clear()
    await message.answer("Вы вернулись в главное меню.", reply_markup=get_main_keyboard())
    
async def run_s3_sync():
    added_count = 0
    updated_count = 0
    
    async with aiosqlite.connect("database.db") as db:
        all_keys = await storage.get_all_files()
        for key in all_keys:
            if not key.endswith('.md') and not key.endswith('.txt'):
                continue
                
            parts = key.split('/')
            filename = parts[-1]
            category = "/".join(parts[:-1]) if len(parts) > 1 else ""
            
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
            except Exception:
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