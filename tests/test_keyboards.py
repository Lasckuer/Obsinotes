from app.keyboards.reply import (
    get_main_keyboard,
    get_cancel_keyboard,
    get_numbers_kb,
    get_back_menu_kb,
    MAIN_MENU_BUTTONS,
    CANCEL_BUTTONS,
    ALL_NAVIGATION_BUTTONS
)
from app.keyboards.inline import (
    get_search_results_inline_kb,
    get_note_actions_keyboard,
    get_delete_confirm_keyboard,
    get_tags_keyboard,
    get_status_keyboard,
    get_daily_format_keyboard
)

def test_main_keyboard():
    kb = get_main_keyboard()
    assert len(kb.keyboard) == 4
    # Row 1
    assert kb.keyboard[0][0].text == "📝 Новая заметка"
    assert kb.keyboard[0][1].text == "🔍 Поиск заметок"
    # Row 2
    assert kb.keyboard[1][0].text == "🤖 Спросить ИИ"
    assert kb.keyboard[1][1].text == "🏷️ Каталог тегов"
    # Row 3
    assert kb.keyboard[2][0].text == "📅 Ежедневник"
    assert kb.keyboard[2][1].text == "💰 Расходы"
    # Row 4
    assert kb.keyboard[3][0].text == "🔄 Синхронизация S3"
    assert kb.keyboard[3][1].text == "⚙️ Статус и справка"

def test_navigation_constants():
    assert "📝 Новая заметка" in MAIN_MENU_BUTTONS
    assert "📝 Отправить заметку" in MAIN_MENU_BUTTONS
    assert "🔍 Поиск заметок" in MAIN_MENU_BUTTONS
    assert "🔍 Поиск / 🤖 ИИ" in MAIN_MENU_BUTTONS
    assert "🔙 В главное меню" in CANCEL_BUTTONS
    assert "❌ Отмена" in CANCEL_BUTTONS
    assert len(ALL_NAVIGATION_BUTTONS) >= 15


def test_cancel_keyboard():
    kb = get_cancel_keyboard()
    assert len(kb.keyboard) == 1
    assert kb.keyboard[0][0].text == "🔙 В главное меню"

def test_get_back_menu_kb_alias():
    kb = get_back_menu_kb()
    assert kb.keyboard[0][0].text == "🔙 В главное меню"

def test_numbers_keyboard():
    kb = get_numbers_kb(7)
    buttons = [btn.text for row in kb.keyboard for btn in row]
    assert "1" in buttons
    assert "7" in buttons
    assert "8" not in buttons
    assert "🔙 В главное меню" in buttons

def test_inline_keyboards():
    # 1. Search results
    search_kb = get_search_results_inline_kb(5)
    search_btns = [btn.text for row in search_kb.inline_keyboard for btn in row]
    assert search_btns == ["1", "2", "3", "4", "5"]

    # 2. Note action buttons (Obsidian URI, Download, Delete)
    action_kb = get_note_actions_keyboard("Планы.md", "TelegramBot/Notes", note_id=42, vault="Notes")
    rows = action_kb.inline_keyboard
    assert len(rows) == 2
    # Row 1: URL button
    assert rows[0][0].text == "🔗 Открыть в Obsidian"
    assert "obsidian://open?vault=Notes&file=TelegramBot/Notes/" in rows[0][0].url
    assert "%D0%9F%D0%BB%D0%B0%D0%BD%D1%8B.md" in rows[0][0].url
    # Row 2: Download and Delete buttons
    assert rows[1][0].text == "📥 Скачать .md"
    assert rows[1][0].callback_data == "dl_note:42"
    assert rows[1][1].text == "🗑 Удалить"
    assert rows[1][1].callback_data == "del_note:42"

    # 3. Delete confirmation keyboard
    confirm_kb = get_delete_confirm_keyboard(42)
    conf_btns = [(b.text, b.callback_data) for row in confirm_kb.inline_keyboard for b in row]
    assert ("✅ Да, удалить", "del_confirm:42") in conf_btns
    assert ("❌ Отмена", "del_cancel:42") in conf_btns

    # 4. Tags keyboard
    tags = [("работа", 5), ("личное", 3)]
    tags_kb = get_tags_keyboard(tags)
    tag_btns = [(b.text, b.callback_data) for row in tags_kb.inline_keyboard for b in row]
    assert ("#работа (5)", "tag:работа") in tag_btns
    assert ("#личное (3)", "tag:личное") in tag_btns

    # 5. Status keyboard
    status_kb = get_status_keyboard()
    status_cbs = [b.callback_data for row in status_kb.inline_keyboard for b in row]
    assert "test_ai" in status_cbs
    assert "sync_now" in status_cbs

    # 6. Daily format keyboard
    daily_kb = get_daily_format_keyboard()
    daily_cbs = [b.callback_data for row in daily_kb.inline_keyboard for b in row]
    assert "daily_fmt:note" in daily_cbs
    assert "daily_fmt:todo" in daily_cbs
    assert "daily_fmt:idea" in daily_cbs


