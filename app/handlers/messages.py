from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.services.llm import process_text
from app.services.yandex_disk import YaDiskService
from app.services.scraper import fetch_url_content
from app.database.db import add_reminder, add_expense, add_note_log
from app.keyboards.reply import get_cancel_keyboard, get_main_keyboard
import datetime
import uuid
import re

router = Router()
ya_disk = YaDiskService()

class NoteState(StatesGroup):
    waiting_for_text = State()

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
    md_filename = f"photo_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
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
        md_filename = f"{base_name}_{datetime.datetime.now().strftime('%H%M%S')}.md"
        
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
    md_filename = f"{base_name}_{datetime.datetime.now().strftime('%H%M%S')}.md"
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