import aiosqlite
import datetime

async def init_db():
    async with aiosqlite.connect("database.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                text TEXT,
                remind_time TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                comment TEXT,
                date TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS notes_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                category TEXT,
                tags TEXT,
                content TEXT,
                date TIMESTAMP
            )
        """)
        await db.commit()

async def add_reminder(user_id: int, text: str, remind_time: str):
    async with aiosqlite.connect("database.db") as db:
        await db.execute("INSERT INTO reminders (user_id, text, remind_time) VALUES (?, ?, ?)", (user_id, text, remind_time))
        await db.commit()

async def get_due_reminders(current_time: str):
    async with aiosqlite.connect("database.db") as db:
        async with db.execute("SELECT id, user_id, text FROM reminders WHERE remind_time <= ?", (current_time,)) as cursor:
            return await cursor.fetchall()

async def delete_reminder(reminder_id: int):
    async with aiosqlite.connect("database.db") as db:
        await db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        await db.commit()

async def add_expense(user_id: int, amount: float, comment: str):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect("database.db") as db:
        await db.execute("INSERT INTO expenses (user_id, amount, comment, date) VALUES (?, ?, ?, ?)", (user_id, amount, comment, now))
        await db.commit()

async def add_note_log(filename: str, category: str, tags: str, content: str):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect("database.db") as db:
        await db.execute("INSERT INTO notes_log (filename, category, tags, content, date) VALUES (?, ?, ?, ?, ?)", (filename, category, tags, content, now))
        await db.commit()

async def search_notes(query: str, is_tag: bool = False):
    query_lower = query.lower()
    
    async with aiosqlite.connect("database.db") as db:
        async with db.execute("SELECT filename, category, tags, content FROM notes_log") as cursor:
            rows = await cursor.fetchall()
            
    results = []
    for row in rows:
        filename, category, tags, content = row
        
        safe_tags = tags.lower() if tags else ""
        safe_content = content.lower() if content else ""
        safe_filename = filename.lower() if filename else ""
        
        if is_tag:
            if query_lower in safe_tags:
                results.append((filename, category))
        else:
            if query_lower in safe_content or query_lower in safe_filename:
                results.append((filename, category))
                
    return results

async def get_today_notes():
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect("database.db") as db:
        async with db.execute("SELECT filename, category FROM notes_log WHERE date LIKE ?", (f"{today}%",)) as cursor:
            return await cursor.fetchall()
        
async def get_recent_context(limit: int = 10):
    """Получает последние записи для контекста ИИ"""
    async with aiosqlite.connect("database.db") as db:
        async with db.execute(
            "SELECT category, content, date FROM notes_log ORDER BY date DESC LIMIT ?", 
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            context = ""
            for cat, cont, date in rows:
                context += f"[{date}] Категория: {cat}\nКонтент: {cont}\n---\n"
            return context