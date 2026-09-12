from app.keyboards.reply import get_main_keyboard, get_cancel_keyboard, get_numbers_kb, get_back_menu_kb

def test_main_keyboard():
    kb = get_main_keyboard()
    assert len(kb.keyboard) == 1
    assert kb.keyboard[0][0].text == "📝 Отправить заметку"
    assert kb.keyboard[0][1].text == "🔍 Поиск / 🤖 ИИ"

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
