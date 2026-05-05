from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.services.llm import process_text, answer_question, summarize_document
from app.services.yandex_disk import YaDiskService
from app.services.scraper import fetch_url_content
from app.database.db import add_reminder, add_expense, add_note_log, get_recent_context
from app.keyboards.reply import get_cancel_keyboard, get_main_keyboard
from docx import Document as DocxReader
from aiogram.fsm.state import State, StatesGroup
import datetime
import uuid
import re
import fitz
import io


router = Router()
ya_disk = YaDiskService()

class NoteState(StatesGroup):
    waiting_for_text = State()
    
class BotState(StatesGroup):
    waiting_for_question = State()

@router.message(F.text == "🔙 В главное меню")
async def cmd_back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Вы вернулись в главное меню.", reply_markup=get_main_keyboard())

@router.message(F.text == "📝 Отправить заметку")
async def prompt_note(message: Message, state: FSMContext):
    await message.answer("Напиши текст, скинь ссылку или фото:", reply_markup=get_cancel_keyboard())
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
    await ya_disk.upload_file("Attachments", filename_img, downloaded_file.read())
    
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

    await ya_disk.upload_file(category, md_filename, md_content.encode('utf-8'))
    await add_note_log(md_filename, category, tags_str, corrected_text)
    
    await processing_msg.edit_text(f"Фото ({md_filename}) успешно сохранено в {category}.")
    await state.clear()
    await message.answer("Вы в главном меню.", reply_markup=get_main_keyboard())

@router.message(NoteState.waiting_for_text, F.text)
async def handle_text(message: Message, state: FSMContext):
    processing_msg = await message.answer("Анализирую...")
    
    async def on_delay():
        try:
            await processing_msg.edit_text("⚠️ Извините за задержку, пробую достучаться еще раз...")
        except Exception:
            pass

    url_content = ""
    url_match = re.search(r'(https?://\S+)', message.text)
    if url_match:
        url_content = await fetch_url_content(url_match.group(1))

    processed = await process_text(message.text, delay_callback=on_delay, url_content=url_content)
    
    category = processed.get("category", "Notes")
    corrected_text = processed.get("corrected_text", message.text)
    tags_list = processed.get("tags", [])
    tags_str = ", ".join(tags_list)
    reminder_time = processed.get("reminder_time")
    
    if category == "Finance" and processed.get("expense_amount"):
        await add_expense(message.from_user.id, float(processed["expense_amount"]), processed.get("expense_comment", ""))
    
    base_name = processed.get("filename", "note")
    timestamp = datetime.datetime.now().strftime("%H%M%S")
    md_filename = f"{base_name}.md"
    md_content = f"---\ntags: [{tags_str}]\ndate: {datetime.datetime.now().strftime('%Y-%m-%d')}\n---\n\n{corrected_text}"

    await ya_disk.upload_file(category, md_filename, md_content.encode('utf-8'))
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
    
    await ya_disk.upload_file("Attachments", doc.file_name, content)
    
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
        
            await ya_disk.upload_file("Notes", fname, full_text.encode('utf-8'))
            await add_note_log(fname, "Notes", tags, summary)
            
            await processing_msg.edit_text(f"✅ Документ изучен и сохранен в `Notes/{fname}`")
        else:
            await processing_msg.edit_text("❌ Не удалось проанализировать текст документа.")
    else:
        await processing_msg.edit_text("❌ Не удалось извлечь текст из файла.")

    await state.clear()
    await message.answer("Вы вернулись в главное меню.", reply_markup=get_main_keyboard())

@router.message(F.text == "🎨 Создать холст (Canvas)")
async def handle_canvas(message: Message, state: FSMContext):
    await state.clear()
    
    from app.database.db import get_today_notes
    from app.services.canvas import create_daily_canvas
    
    notes = await get_today_notes()
    if not notes:
        return await message.answer("Сегодня еще нет заметок для создания холста. Сначала добавь что-нибудь!")
    
    msg = await message.answer("🚀 Генерирую холст со всеми заметками за сегодня...")
    
    canvas_json = create_daily_canvas(notes)
    canvas_name = f"Daily_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.canvas"

    
    await ya_disk.upload_file("Notes", canvas_name, canvas_json.encode('utf-8'))
    await msg.edit_text(f"✨ Холст `{canvas_name}` успешно создан в папке Notes! Теперь он доступен в Obsidian.")

@router.message(F.text == "🤖 Спросить ИИ")
async def ask_ai_start(message: Message, state: FSMContext):
    await message.answer("Задай вопрос по своим заметкам (например: 'Когда была последняя тренировка?'):", reply_markup=get_cancel_keyboard())
    await state.set_state(BotState.waiting_for_question)

@router.message(BotState.waiting_for_question)
async def ask_ai_handler(message: Message, state: FSMContext):
    context = await get_recent_context(20)
    answer = await answer_question(message.text, context)
    await message.answer(answer, reply_markup=get_main_keyboard())
    await state.clear()
