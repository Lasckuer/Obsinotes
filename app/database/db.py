import aiosqlite

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
        await db.commit()

async def add_reminder(user_id: int, text: str, remind_time: str):
    async with aiosqlite.connect("database.db") as db:
        await db.execute(
            "INSERT INTO reminders (user_id, text, remind_time) VALUES (?, ?, ?)",
            (user_id, text, remind_time)
        )
        await db.commit()

async def get_due_reminders(current_time: str):
    async with aiosqlite.connect("database.db") as db:
        async with db.execute(
            "SELECT id, user_id, text FROM reminders WHERE remind_time <= ?",
            (current_time,)
        ) as cursor:
            return await cursor.fetchall()

async def delete_reminder(reminder_id: int):
    async with aiosqlite.connect("database.db") as db:
        await db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        await db.commit()