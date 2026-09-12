import aiosqlite
import datetime
import re
from typing import List, Tuple, Optional
from contextlib import asynccontextmanager
from app.config import get_settings
from logger import logger, log_db_error

class DatabaseManager:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or get_settings().DB_PATH
        self._conn: Optional[aiosqlite.Connection] = None
        self._fts_available: bool = False

    async def get_connection(self) -> aiosqlite.Connection:
        # For :memory: databases (e.g. during testing), keep a single persistent connection
        if self.db_path == ":memory:":
            if self._conn is None:
                self._conn = await aiosqlite.connect(self.db_path)
                await self._conn.execute("PRAGMA foreign_keys = ON;")
            return self._conn
        
        # For file-based databases, create or reuse connection
        if self._conn is None:
            self._conn = await aiosqlite.connect(self.db_path)
            await self._conn.execute("PRAGMA journal_mode = WAL;")
            await self._conn.execute("PRAGMA synchronous = NORMAL;")
            await self._conn.execute("PRAGMA foreign_keys = ON;")
        return self._conn

    async def close(self):
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def init_db(self):
        conn = await self.get_connection()
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                remind_time TIMESTAMP NOT NULL,
                recur_minutes INTEGER DEFAULT 0
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                comment TEXT,
                date TIMESTAMP NOT NULL
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS notes_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                category TEXT NOT NULL,
                tags TEXT,
                content TEXT,
                date TIMESTAMP NOT NULL
            )
        """)

        # Create indexes for high-performance querying
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_time ON reminders(remind_time)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_log_date ON notes_log(date)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_log_file_cat ON notes_log(filename, category)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_expenses_user ON expenses(user_id, date)")

        # Full-Text Search (FTS5) table and triggers for notes
        try:
            await conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                    filename,
                    category,
                    tags,
                    content,
                    content='notes_log',
                    content_rowid='id'
                )
            """)

            # Triggers to keep FTS table in sync
            await conn.execute("""
                CREATE TRIGGER IF NOT EXISTS notes_log_ai AFTER INSERT ON notes_log BEGIN
                    INSERT INTO notes_fts(rowid, filename, category, tags, content)
                    VALUES (new.id, new.filename, new.category, new.tags, new.content);
                END;
            """)
            await conn.execute("""
                CREATE TRIGGER IF NOT EXISTS notes_log_ad AFTER DELETE ON notes_log BEGIN
                    INSERT INTO notes_fts(notes_fts, rowid, filename, category, tags, content)
                    VALUES ('delete', old.id, old.filename, old.category, old.tags, old.content);
                END;
            """)
            await conn.execute("""
                CREATE TRIGGER IF NOT EXISTS notes_log_au AFTER UPDATE ON notes_log BEGIN
                    INSERT INTO notes_fts(notes_fts, rowid, filename, category, tags, content)
                    VALUES ('delete', old.id, old.filename, old.category, old.tags, old.content);
                    INSERT INTO notes_fts(rowid, filename, category, tags, content)
                    VALUES (new.id, new.filename, new.category, new.tags, new.content);
                END;
            """)
            self._fts_available = True
        except Exception as e:
            logger.warning(f"FTS5 не поддерживается или произошла ошибка инициализации: {e}. Используется поиск LIKE.")
            self._fts_available = False

        await conn.commit()

    async def add_reminder(self, user_id: int, text: str, remind_time: str, recur_minutes: int = 0):
        conn = await self.get_connection()
        await conn.execute(
            "INSERT INTO reminders (user_id, text, remind_time, recur_minutes) VALUES (?, ?, ?, ?)",
            (user_id, text, remind_time, recur_minutes)
        )
        await conn.commit()

    async def get_due_reminders(self, current_time: str) -> List[Tuple[int, int, str, int]]:
        conn = await self.get_connection()
        async with conn.execute(
            "SELECT id, user_id, text, recur_minutes FROM reminders WHERE remind_time <= ?",
            (current_time,)
        ) as cursor:
            return await cursor.fetchall()

    async def delete_reminder(self, reminder_id: int):
        conn = await self.get_connection()
        await conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        await conn.commit()

    async def update_reminder_time(self, r_id: int, next_time: str):
        conn = await self.get_connection()
        await conn.execute("UPDATE reminders SET remind_time = ? WHERE id = ?", (next_time, r_id))
        await conn.commit()

    async def add_expense(self, user_id: int, amount: float, comment: str):
        conn = await self.get_connection()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await conn.execute(
            "INSERT INTO expenses (user_id, amount, comment, date) VALUES (?, ?, ?, ?)",
            (user_id, amount, comment, now_str)
        )
        await conn.commit()

    async def add_note_log(self, filename: str, category: str, tags: str, content: str):
        conn = await self.get_connection()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await conn.execute(
            "INSERT INTO notes_log (filename, category, tags, content, date) VALUES (?, ?, ?, ?, ?)",
            (filename, category, tags, content, now_str)
        )
        await conn.commit()

    async def upsert_note_log(self, filename: str, category: str, tags: str, content: str) -> Tuple[bool, bool]:
        """
        Inserts or updates a note log entry.
        Returns: (is_added, is_updated)
        """
        conn = await self.get_connection()
        async with conn.execute(
            "SELECT id, content FROM notes_log WHERE filename = ? AND category = ?",
            (filename, category)
        ) as cursor:
            row = await cursor.fetchone()

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not row:
            await conn.execute(
                "INSERT INTO notes_log (filename, category, tags, content, date) VALUES (?, ?, ?, ?, ?)",
                (filename, category, tags, content, now_str)
            )
            await conn.commit()
            return True, False
        else:
            note_id, old_content = row[0], row[1]
            if old_content != content:
                await conn.execute(
                    "UPDATE notes_log SET content = ?, tags = ?, date = ? WHERE id = ?",
                    (content, tags or "", now_str, note_id)
                )
                await conn.commit()
                return False, True
            return False, False

    async def note_exists(self, filename: str, category: str) -> bool:
        conn = await self.get_connection()
        async with conn.execute(
            "SELECT 1 FROM notes_log WHERE filename = ? AND category LIKE ? LIMIT 1",
            (filename, f"%{category}")
        ) as cursor:
            return (await cursor.fetchone()) is not None

    async def search_notes(self, query: str, limit: int = 50) -> List[Tuple[str, str]]:
        clean_query = query.strip().replace("#", "")
        if not clean_query:
            return []

        conn = await self.get_connection()

        # 1. Attempt FTS5 search if available
        if self._fts_available:
            try:
                # Sanitize query for FTS5 syntax
                fts_query = ' '.join(f'"{token}"*' for token in re.findall(r'\w+', clean_query))
                if fts_query:
                    async with conn.execute(
                        "SELECT filename, category FROM notes_fts WHERE notes_fts MATCH ? LIMIT ?",
                        (fts_query, limit)
                    ) as cursor:
                        rows = await cursor.fetchall()
                        if rows:
                            return rows
            except Exception as e:
                logger.debug(f"FTS5 query failed: {e}. Falling back to LIKE.")

        # 2. Fallback to SQL LIKE search
        like_pattern = f"%{clean_query}%"
        async with conn.execute(
            """
            SELECT filename, category FROM notes_log
            WHERE filename LIKE ? OR tags LIKE ? OR content LIKE ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (like_pattern, like_pattern, like_pattern, limit)
        ) as cursor:
            return await cursor.fetchall()

    async def get_today_notes(self) -> List[Tuple[str, str]]:
        today_prefix = datetime.datetime.now().strftime("%Y-%m-%d")
        conn = await self.get_connection()
        async with conn.execute(
            "SELECT filename, category FROM notes_log WHERE date LIKE ? ORDER BY date DESC",
            (f"{today_prefix}%",)
        ) as cursor:
            return await cursor.fetchall()

    async def get_recent_context(self, limit: int = 10) -> str:
        conn = await self.get_connection()
        async with conn.execute(
            "SELECT category, content, date FROM notes_log ORDER BY date DESC LIMIT ?",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return "".join(f"[{date}] [{cat}]: {cont}\n" for cat, cont, date in rows)

    async def get_recent_notes_for_linking(self, limit: int = 10) -> str:
        conn = await self.get_connection()
        async with conn.execute(
            "SELECT filename, content FROM notes_log ORDER BY date DESC LIMIT ?",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            items = []
            for fname, cont in rows:
                if not fname:
                    continue
                clean_name = fname[:-3] if fname.endswith(".md") else fname
                snippet = cont[:150].replace('\n', ' ') if cont else ""
                items.append(f"- [[{clean_name}]]: {snippet}...\n")
            return "".join(items)


# Default module-level instance
db_manager = DatabaseManager()

# Module-level convenience wrappers for backwards compatibility
async def init_db():
    await db_manager.init_db()

async def add_reminder(user_id: int, text: str, remind_time: str, recur_minutes: int = 0):
    await db_manager.add_reminder(user_id, text, remind_time, recur_minutes)

async def get_due_reminders(current_time: str):
    return await db_manager.get_due_reminders(current_time)

async def delete_reminder(reminder_id: int):
    await db_manager.delete_reminder(reminder_id)

async def update_reminder_time(r_id: int, next_time: str):
    await db_manager.update_reminder_time(r_id, next_time)

async def add_expense(user_id: int, amount: float, comment: str):
    await db_manager.add_expense(user_id, amount, comment)

async def add_note_log(filename: str, category: str, tags: str, content: str):
    await db_manager.add_note_log(filename, category, tags, content)

async def upsert_note_log(filename: str, category: str, tags: str, content: str) -> Tuple[bool, bool]:
    return await db_manager.upsert_note_log(filename, category, tags, content)

async def note_exists(filename: str, category: str) -> bool:
    return await db_manager.note_exists(filename, category)

async def search_notes(query: str, limit: int = 50):
    return await db_manager.search_notes(query, limit)

async def get_today_notes():
    return await db_manager.get_today_notes()

async def get_recent_context(limit: int = 10):
    return await db_manager.get_recent_context(limit)

async def get_recent_notes_for_linking(limit: int = 10) -> str:
    return await db_manager.get_recent_notes_for_linking(limit)

async def close_db():
    await db_manager.close()