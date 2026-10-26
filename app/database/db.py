import os
import aiosqlite
import datetime
import re
from typing import List, Tuple, Optional, Dict, Any, Union
from contextlib import asynccontextmanager
from app.config import get_settings
from logger import logger, log_db_error

def strip_frontmatter(content: Optional[str]) -> str:
    """Strips YAML frontmatter (including leading BOM, empty frontmatter, and --- / ... delimiters) from markdown text."""
    if not content:
        return ""
    text = content.lstrip('\ufeff')
    cleaned = re.sub(r'^\s*---\s*(?:[\r\n]+.*?[\r\n]+|[\r\n]+)(?:---|\.\.\.)\s*[\r\n]*', '', text, flags=re.DOTALL)
    return cleaned.strip()

def extract_tags_from_note(content: Optional[str]) -> str:
    """Extracts tags from YAML frontmatter (brackets, list, or inline) and inline #hashtags."""
    if not content:
        return ""
    tags = set()
    text = content.lstrip('\ufeff')
    fm_match = re.search(r'^\s*---\s*[\r\n]+(.*?)[\r\n]+(?:---|\.\.\.)', text, flags=re.DOTALL)
    if fm_match:
        fm_text = fm_match.group(1)
        # 1. tags: [a, b]
        bracket_match = re.search(r'tags:\s*\[(.*?)\]', fm_text, flags=re.IGNORECASE)
        if bracket_match:
            for t in bracket_match.group(1).split(','):
                clean_t = t.strip().strip('"\'').lstrip('#')
                if clean_t:
                    tags.add(clean_t)
        # 2. tags: a, b
        inline_match = re.search(r'tags:\s*([^\r\n\[\-]+)', fm_text, flags=re.IGNORECASE)
        if inline_match and not bracket_match:
            for t in inline_match.group(1).split(','):
                clean_t = t.strip().strip('"\'').lstrip('#')
                if clean_t:
                    tags.add(clean_t)
        # 3. tags:\n  - a\n  - b
        list_items = re.findall(r'^\s*-\s+([^\r\n]+)', fm_text, flags=re.MULTILINE)
        for item in list_items:
            clean_item = item.strip().strip('"\'').lstrip('#')
            if clean_item:
                tags.add(clean_item)

    # 4. Also scan for #tag in content (excluding markdown headings # Header)
    hash_tags = re.findall(r'(?:^|\s)#([a-zA-Zа-яА-ЯёЁ0-9_\-]+)', text)
    for ht in hash_tags:
        tags.add(ht)

    return ", ".join(sorted(tags))

class RAGNote(dict):
    """
    Dictionary containing note metadata and clean content,
    also supports tuple-like unpacking (filename, category, content, date),
    length check len(note) == 4, and integer indexing note[0], note[1], note[2], note[3].
    """
    def __init__(
        self,
        filename: str,
        category: str,
        content: str,
        date: str,
        tags: str = "",
        note_id: Optional[int] = None
    ):
        super().__init__(
            id=note_id,
            filename=filename,
            category=category,
            tags=tags,
            content=content,
            date=date
        )

    def __iter__(self):
        return iter((self["filename"], self["category"], self["content"], self["date"]))

    def __len__(self):
        return 4

    def __getitem__(self, item):
        if isinstance(item, int):
            tuple_vals = (self["filename"], self["category"], self["content"], self["date"])
            return tuple_vals[item]
        return super().__getitem__(item)

class SearchResult(tuple):
    """
    A 2-tuple (filename, category) that also provides snippet and filename/category attributes.
    Unpacks as (filename, category) for 100% backwards compatibility with existing callers and tests.
    """
    def __new__(cls, filename: str, category: str, snippet: str = ""):
        obj = super().__new__(cls, (filename, category))
        obj.snippet = snippet
        obj.filename = filename
        obj.category = category
        return obj

    def __repr__(self):
        return f"SearchResult(filename={self.filename!r}, category={self.category!r}, snippet={self.snippet!r})"

SEARCH_STOP_WORDS = {
    # Russian conversational prefixes & stop words
    "найди", "найти", "ищи", "поищи", "поиск", "искать",
    "заметки", "заметку", "заметка", "записей", "записи", "запись",
    "про", "о", "обо", "по", "покажи", "показать", "открой", "открыть",
    "где", "что", "там", "в", "во", "на", "с", "со", "к", "ко", "из",
    "мне", "мои", "моя", "мое", "мой", "все", "всё", "как",
    # English
    "find", "search", "note", "notes", "show", "about", "in", "my", "all"
}

def clean_search_query(query: str) -> List[str]:
    """Tokenizes search query and filters conversational prefixes and stop words."""
    clean_text = query.strip().replace("#", " ")
    raw_tokens = [t.strip('-') for t in re.findall(r'[\w\-]+', clean_text) if t.strip('-')]
    tokens = [t for t in raw_tokens if len(t) >= 2]
    meaningful = [t for t in tokens if t.lower() not in SEARCH_STOP_WORDS]
    if meaningful:
        return meaningful
    if tokens:
        return tokens
    if raw_tokens:
        return raw_tokens
    stripped = query.strip()
    return [stripped] if stripped else []

def sanitize_fts_token(token: str) -> str:
    """Strips FTS5 special operators to avoid query syntax errors."""
    return re.sub(r'[^\w]', '', token)

def extract_snippet(content: str, keywords: List[str], max_chars: int = 120) -> str:
    """Extracts a short preview of the text around the matched keywords, stripping frontmatter."""
    clean = strip_frontmatter(content).replace("\r\n", " ").replace("\n", " ").strip()
    if not clean:
        return ""

    earliest_pos = -1
    for kw in keywords:
        pos = clean.lower().find(kw.lower())
        if pos != -1 and (earliest_pos == -1 or pos < earliest_pos):
            earliest_pos = pos

    if earliest_pos == -1:
        snippet = clean[:max_chars].strip()
        return f"{snippet}..." if len(clean) > max_chars else snippet

    start = max(0, earliest_pos - 30)
    end = min(len(clean), earliest_pos + max_chars - 30)
    snippet = clean[start:end].strip()
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(clean) else ""
    return f"{prefix}{snippet}{suffix}"

def get_search_stem(word: str) -> str:
    """Strips common Russian inflectional endings for better keyword matching across cases."""
    w = word.lower().strip()
    if len(w) < 4:
        return w
    ENDINGS = (
        'ями', 'ами', 'ого', 'его', 'ому', 'ему', 'ыми', 'ими', 'ых', 'их',
        'ей', 'ов', 'ев', 'ах', 'ях', 'ом', 'ем', 'ой', 'ею', 'ью',
        'ам', 'ям', 'а', 'я', 'о', 'е', 'ы', 'и', 'у', 'ю'
    )
    for end in ENDINGS:
        if w.endswith(end) and len(w) - len(end) >= 3:
            return w[:-len(end)]
    return w



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
            dir_name = os.path.dirname(self.db_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            self._conn = await aiosqlite.connect(self.db_path)
            await self._conn.execute("PRAGMA journal_mode = WAL;")
            await self._conn.execute("PRAGMA synchronous = NORMAL;")
            await self._conn.execute("PRAGMA foreign_keys = ON;")
        return self._conn

    async def close(self):
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _ensure_fts_status(self, conn: aiosqlite.Connection) -> bool:
        if not self._fts_available:
            try:
                async with conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='notes_fts'"
                ) as cursor:
                    if await cursor.fetchone():
                        self._fts_available = True
            except Exception:
                pass
        return self._fts_available

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
                date TIMESTAMP NOT NULL,
                etag TEXT,
                last_modified TIMESTAMP
            )
        """)

        # Automatic schema migration for existing databases
        try:
            cursor = await conn.execute("PRAGMA table_info(notes_log);")
            columns = [row[1] for row in await cursor.fetchall()]
            if "etag" not in columns:
                await conn.execute("ALTER TABLE notes_log ADD COLUMN etag TEXT;")
            if "last_modified" not in columns:
                await conn.execute("ALTER TABLE notes_log ADD COLUMN last_modified TIMESTAMP;")
            await conn.commit()
        except Exception as e:
            logger.warning(f"Error checking/migrating notes_log schema: {e}")

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
            # Ensure existing rows in notes_log are indexed in FTS without corrupting b-tree on delete
            await conn.execute("INSERT INTO notes_fts(notes_fts) VALUES('rebuild');")
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

    async def add_note_log(
        self,
        filename: str,
        category: str,
        tags: str,
        content: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        commit: bool = True
    ) -> int:
        conn = await self.get_connection()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = await conn.execute(
            """
            INSERT INTO notes_log (filename, category, tags, content, date, etag, last_modified)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (filename, category, tags, content, now_str, etag, str(last_modified) if last_modified is not None else None)
        )
        if commit:
            await conn.commit()
        return cursor.lastrowid


    async def upsert_note_log(
        self,
        filename: str,
        category: str,
        tags: str,
        content: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        commit: bool = True
    ) -> Tuple[bool, bool]:
        """
        Inserts or updates a note log entry.
        Preserves existing tags if new tags are empty, or extracts tags from content frontmatter.
        Returns: (is_added, is_updated)
        """
        clean_cat = category or ""
        conn = await self.get_connection()
        async with conn.execute(
            "SELECT id, content, etag, last_modified, tags FROM notes_log WHERE filename = ? AND (category = ? OR (? = '' AND (category IS NULL OR category = 'Root')))",
            (filename, clean_cat, clean_cat)
        ) as cursor:
            row = await cursor.fetchone()

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lm_str = str(last_modified) if last_modified is not None else None
        inferred_tags = tags or extract_tags_from_note(content)

        if not row:
            await conn.execute(
                """
                INSERT INTO notes_log (filename, category, tags, content, date, etag, last_modified)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (filename, clean_cat, inferred_tags, content, now_str, etag, lm_str)
            )
            if commit:
                await conn.commit()
            return True, False
        else:
            note_id, old_content, old_etag, old_lm, old_tags = row[0], row[1], row[2], row[3], row[4]
            content_changed = (old_content != content)
            etag_changed = (etag is not None and old_etag != etag)
            lm_changed = (lm_str is not None and str(old_lm) != lm_str)
            final_tags = inferred_tags or old_tags or ""
            tags_changed = (final_tags != (old_tags or ""))

            if content_changed or etag_changed or lm_changed or tags_changed:
                await conn.execute(
                    """
                    UPDATE notes_log
                    SET content = ?, tags = ?, date = ?, etag = ?, last_modified = ?
                    WHERE id = ?
                    """,
                    (content, final_tags, now_str, etag or old_etag, lm_str or old_lm, note_id)
                )
                if commit:
                    await conn.commit()
                return False, True
            return False, False

    async def commit(self):
        conn = await self.get_connection()
        await conn.commit()

    async def delete_notes(self, file_cat_pairs: List[Tuple[str, str]], commit: bool = True) -> int:
        """
        Deletes notes by (filename, category) pairs.
        Trigger automatically keeps notes_fts in sync.
        Returns the number of deleted records.
        """
        if not file_cat_pairs:
            return 0
        conn = await self.get_connection()
        deleted_total = 0
        for filename, category in file_cat_pairs:
            cat = category or ""
            cursor = await conn.execute(
                "DELETE FROM notes_log WHERE filename = ? AND (category = ? OR (? = '' AND (category IS NULL OR category = 'Root')))",
                (filename, cat, cat)
            )
            deleted_total += cursor.rowcount
        if commit:
            await conn.commit()
        return deleted_total

    async def get_all_notes_metadata(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """
        Returns a dict mapping (category, filename) -> metadata dict.
        """
        conn = await self.get_connection()
        try:
            async with conn.execute(
                "SELECT id, filename, category, etag, last_modified FROM notes_log"
            ) as cursor:
                rows = await cursor.fetchall()
                result = {}
                for row in rows:
                    cat = row[2] or ""
                    result[(cat, row[1])] = {
                        "id": row[0],
                        "filename": row[1],
                        "category": cat,
                        "etag": row[3],
                        "last_modified": str(row[4]) if row[4] is not None else None
                    }
                return result
        except Exception as e:
            logger.warning(f"Error fetching notes metadata: {e}")
            return {}

    async def note_exists(self, filename: str, category: str) -> bool:
        conn = await self.get_connection()
        if not category or category == "Root":
            sql = "SELECT 1 FROM notes_log WHERE filename = ? AND (category = '' OR category IS NULL OR category = 'Root') LIMIT 1"
            params = (filename,)
        else:
            sql = "SELECT 1 FROM notes_log WHERE filename = ? AND (category = ? OR category LIKE ?) LIMIT 1"
            params = (filename, category, f"%{category}")
        async with conn.execute(sql, params) as cursor:
            return (await cursor.fetchone()) is not None

    async def search_notes(self, query: str, limit: int = 50) -> List[SearchResult]:
        keywords = clean_search_query(query)
        if not keywords:
            return []

        conn = await self.get_connection()
        await self._ensure_fts_status(conn)

        # Build search terms with stems for flexible matching across inflectional endings
        search_terms = []
        for kw in keywords:
            if kw not in search_terms:
                search_terms.append(kw)
            stem = get_search_stem(kw)
            if stem != kw and stem not in search_terms:
                search_terms.append(stem)

        raw_clean = query.strip().replace("#", "").strip()
        if raw_clean and raw_clean not in search_terms:
            search_terms.append(raw_clean)

        # 1. Attempt FTS5 search if available
        if self._fts_available:
            try:
                valid_tokens = [sanitize_fts_token(t) for t in search_terms if sanitize_fts_token(t)]
                if valid_tokens:
                    fts_query = ' OR '.join(f'"{t}"*' for t in valid_tokens)
                    async with conn.execute(
                        """
                        SELECT nl.filename, nl.category, nl.content
                        FROM notes_fts fts
                        JOIN notes_log nl ON nl.id = fts.rowid
                        WHERE notes_fts MATCH ?
                        ORDER BY fts.rank, nl.date DESC
                        LIMIT ?
                        """,
                        (fts_query, limit)
                    ) as cursor:
                        rows = await cursor.fetchall()
                        if rows:
                            return [SearchResult(r[0], r[1] or "", extract_snippet(r[2] or "", search_terms)) for r in rows]
            except Exception as e:
                logger.debug(f"FTS5 query failed: {e}. Falling back to LIKE.")

        # 2. Fallback to SQL LIKE search
        conditions = []
        params = []
        for term in search_terms[:5]:
            pat = f"%{term}%"
            conditions.append("(filename LIKE ? OR tags LIKE ? OR content LIKE ?)")
            params.extend([pat, pat, pat])

        if conditions:
            sql = f"""
                SELECT filename, category, content FROM notes_log
                WHERE {' OR '.join(conditions)}
                ORDER BY date DESC
                LIMIT ?
            """
            params.append(limit)
            async with conn.execute(sql, tuple(params)) as cursor:
                rows = await cursor.fetchall()
                return [SearchResult(r[0], r[1] or "", extract_snippet(r[2] or "", search_terms)) for r in rows]

        return []

    async def search_notes_with_snippets(self, query: str, limit: int = 25) -> List[Tuple[str, str, str]]:
        """Returns (filename, category, snippet) for matched notes."""
        results = await self.search_notes(query, limit)
        return [(r.filename, r.category, r.snippet) for r in results]

    async def get_total_notes_count(self) -> int:
        """Returns total number of notes indexed in SQLite."""
        conn = await self.get_connection()
        try:
            async with conn.execute("SELECT COUNT(*) FROM notes_log") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
        except Exception:
            return 0

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
                clean_name = fname[:-3] if fname.lower().endswith(".md") else fname
                cleaned_cont = strip_frontmatter(cont)
                snippet = cleaned_cont[:150].replace('\n', ' ') if cleaned_cont else ""
                items.append(f"- [[{clean_name}]]: {snippet}...\n")
            return "".join(items)

    async def search_rag_context(self, query: str, limit: int = 5) -> List[RAGNote]:
        """
        Hybrid RAG context search:
        1. FTS5 matching with BM25 relevance ranking across query keywords (ignoring stop words).
        2. If FTS matches are fewer than limit, supplements with the most recent notes.
        3. Strips YAML frontmatter from all snippets before returning.
        """
        clean_query = query.strip().replace("#", "")
        conn = await self.get_connection()
        await self._ensure_fts_status(conn)

        matched_rows: List[Tuple[Any, ...]] = []
        matched_ids = set()

        STOP_WORDS = {
            "как", "что", "где", "когда", "почему", "зачем",
            "сколько", "кто", "куда", "откуда", "чей", "чья", "чье", "чьи",
            "какой", "какая", "какое", "какие", "какую", "каком", "каких", "каким", "какими",
            "расскажи", "напиши", "спроси", "напомни", "объясни", "подскажи",
            "мой", "моя", "мое", "мои", "моем", "моих", "моего", "моей",
            "мне", "меня", "мной", "мною", "нам", "нас", "нами",
            "про", "для", "обо", "при", "без", "над", "под", "перед",
            "это", "эта", "этот", "эти", "этим", "этих", "этому",
            "есть", "был", "была", "было", "были", "будет", "будут",
            "what", "how", "where", "when", "why", "who", "whom", "which",
            "tell", "about", "show", "explain", "find",
            "my", "me", "we", "our", "us",
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did",
            "for", "with", "from", "into", "during", "including", "until", "against",
            "this", "that", "these", "those"
        }

        tokens = re.findall(r'\w+', clean_query)
        # Filter out common question/stop words when more specific tokens exist
        filtered_tokens = [t for t in tokens if t.lower() not in STOP_WORDS and len(t) > 1]
        search_tokens = filtered_tokens or [t for t in tokens if len(t) > 1] or tokens

        # 1. Attempt FTS5 search with BM25 ranking if available
        if self._fts_available and search_tokens:
            try:
                # Use OR query for BM25 ranking across question terms
                valid_fts_tokens = [sanitize_fts_token(t) for t in search_tokens if sanitize_fts_token(t)]
                if valid_fts_tokens:
                    fts_query = ' OR '.join(f'"{token}"*' for token in valid_fts_tokens)
                    async with conn.execute(
                        """
                        SELECT nl.id, nl.filename, nl.category, nl.tags, nl.content, nl.date
                        FROM notes_fts fts
                        JOIN notes_log nl ON nl.id = fts.rowid
                        WHERE notes_fts MATCH ?
                        ORDER BY fts.rank, nl.date DESC
                        LIMIT ?
                        """,
                        (fts_query, limit)
                    ) as cursor:
                        rows = await cursor.fetchall()
                        for r in rows:
                            if r[0] not in matched_ids:
                                matched_ids.add(r[0])
                                matched_rows.append(r)
            except Exception as e:
                logger.debug(f"FTS5 RAG query failed: {e}. Falling back to LIKE search.")

        # Fallback to LIKE if FTS is unavailable or produced nothing and tokens exist
        if not matched_rows and search_tokens:
            try:
                significant_tokens = [t for t in search_tokens if len(t) >= 3] or search_tokens
                conditions = []
                params = []
                for t in significant_tokens[:5]:
                    pat = f"%{t}%"
                    conditions.append("(filename LIKE ? OR tags LIKE ? OR content LIKE ?)")
                    params.extend([pat, pat, pat])
                if conditions:
                    sql = f"""
                        SELECT id, filename, category, tags, content, date
                        FROM notes_log
                        WHERE {' OR '.join(conditions)}
                        ORDER BY date DESC
                        LIMIT ?
                    """
                    params.append(limit)
                    async with conn.execute(sql, tuple(params)) as cursor:
                        rows = await cursor.fetchall()
                        for r in rows:
                            if r[0] not in matched_ids:
                                matched_ids.add(r[0])
                                matched_rows.append(r)
            except Exception as e:
                logger.debug(f"LIKE RAG search failed: {e}")

        # 2. If matches are fewer than limit, supplement with the most recent notes
        if len(matched_rows) < limit:
            needed = limit - len(matched_rows)
            try:
                if matched_ids:
                    placeholders = ','.join('?' for _ in matched_ids)
                    sql = f"""
                        SELECT id, filename, category, tags, content, date
                        FROM notes_log
                        WHERE id NOT IN ({placeholders})
                        ORDER BY date DESC
                        LIMIT ?
                    """
                    params = tuple(matched_ids) + (needed,)
                else:
                    sql = """
                        SELECT id, filename, category, tags, content, date
                        FROM notes_log
                        ORDER BY date DESC
                        LIMIT ?
                    """
                    params = (needed,)

                async with conn.execute(sql, params) as cursor:
                    supp_rows = await cursor.fetchall()
                    for r in supp_rows:
                        if r[0] not in matched_ids:
                            matched_ids.add(r[0])
                            matched_rows.append(r)
            except Exception as e:
                logger.warning(f"Error supplementing RAG context with recent notes: {e}")

        # 3. Format results, stripping YAML frontmatter from content
        results: List[RAGNote] = []
        for row in matched_rows[:limit]:
            nid, fname, cat, tags, raw_cont, dt = row[0], row[1], row[2], row[3], row[4], row[5]
            clean_cont = strip_frontmatter(raw_cont)
            results.append(RAGNote(
                filename=fname,
                category=cat,
                content=clean_cont,
                date=str(dt) if dt else "",
                tags=tags or "",
                note_id=nid
            ))

        return results

    async def get_note_by_id(self, note_id: int) -> Optional[Dict[str, Any]]:
        """Returns note dictionary by primary key ID."""
        conn = await self.get_connection()
        async with conn.execute(
            "SELECT id, filename, category, tags, content, date FROM notes_log WHERE id = ?",
            (note_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "filename": row[1],
                "category": row[2] or "",
                "tags": row[3] or "",
                "content": row[4] or "",
                "date": str(row[5]) if row[5] else ""
            }

    async def get_note_by_file_and_cat(self, filename: str, category: str) -> Optional[Dict[str, Any]]:
        """Finds note by filename and category."""
        clean_cat = category or ""
        conn = await self.get_connection()
        async with conn.execute(
            "SELECT id, filename, category, tags, content, date FROM notes_log WHERE filename = ? AND (category = ? OR (? = '' AND (category IS NULL OR category = 'Root'))) LIMIT 1",
            (filename, clean_cat, clean_cat)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "filename": row[1],
                "category": row[2] or "",
                "tags": row[3] or "",
                "content": row[4] or "",
                "date": str(row[5]) if row[5] else ""
            }

    async def get_expenses_summary(self, user_id: int) -> Dict[str, Any]:
        """Calculates expense totals and records for today and current month."""
        conn = await self.get_connection()
        today_prefix = datetime.datetime.now().strftime("%Y-%m-%d")
        month_prefix = datetime.datetime.now().strftime("%Y-%m")

        # Today's sum and count
        async with conn.execute(
            "SELECT COALESCE(SUM(amount), 0), COUNT(id) FROM expenses WHERE user_id = ? AND date LIKE ?",
            (user_id, f"{today_prefix}%")
        ) as cursor:
            today_row = await cursor.fetchone()
            today_sum = float(today_row[0]) if today_row else 0.0
            today_count = int(today_row[1]) if today_row else 0

        # Month's sum and count
        async with conn.execute(
            "SELECT COALESCE(SUM(amount), 0), COUNT(id) FROM expenses WHERE user_id = ? AND date LIKE ?",
            (user_id, f"{month_prefix}%")
        ) as cursor:
            month_row = await cursor.fetchone()
            month_sum = float(month_row[0]) if month_row else 0.0
            month_count = int(month_row[1]) if month_row else 0

        # Today's recent records
        async with conn.execute(
            "SELECT amount, comment, date FROM expenses WHERE user_id = ? AND date LIKE ? ORDER BY date DESC LIMIT 10",
            (user_id, f"{today_prefix}%")
        ) as cursor:
            today_records = await cursor.fetchall()

        return {
            "today_sum": today_sum,
            "today_count": today_count,
            "month_sum": month_sum,
            "month_count": month_count,
            "today_records": today_records
        }

    async def get_all_tags_with_counts(self) -> List[Tuple[str, int]]:
        """Returns all distinct tags sorted by frequency desc."""
        conn = await self.get_connection()
        async with conn.execute(
            "SELECT tags FROM notes_log WHERE tags IS NOT NULL AND tags != ''"
        ) as cursor:
            rows = await cursor.fetchall()

        import collections
        tag_counts = collections.Counter()
        for (tags_str,) in rows:
            if not tags_str:
                continue
            for tag in tags_str.split(','):
                clean_tag = tag.strip().strip("[]\"'").lstrip('#').strip().lower()
                if clean_tag:
                    tag_counts[clean_tag] += 1

        return sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))

    async def get_notes_by_tag(self, tag: str, limit: int = 50) -> List[Tuple[str, str]]:
        """
        Returns (filename, category) for notes matching the specified tag.
        Matches exact tag in comma-delimited tag list or as #tag in content.
        """
        clean_tag = tag.strip().strip("[]\"'").lstrip('#').strip().lower()
        if not clean_tag:
            return []
        conn = await self.get_connection()
        hash_pat = f"%#{clean_tag}%"
        async with conn.execute(
            """
            SELECT filename, category FROM notes_log
            WHERE LOWER(tags) = ?
               OR LOWER(tags) LIKE ?
               OR LOWER(tags) LIKE ?
               OR LOWER(tags) LIKE ?
               OR LOWER(content) LIKE ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (
                clean_tag,
                f"{clean_tag}, %",
                f"%, {clean_tag}",
                f"%, {clean_tag}, %",
                hash_pat,
                limit
            )
        ) as cursor:
            return await cursor.fetchall()


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

async def get_expenses_summary(user_id: int) -> Dict[str, Any]:
    return await db_manager.get_expenses_summary(user_id)

async def add_note_log(
    filename: str,
    category: str,
    tags: str,
    content: str,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
    commit: bool = True
) -> int:
    return await db_manager.add_note_log(filename, category, tags, content, etag, last_modified, commit)

async def get_note_by_id(note_id: int) -> Optional[Dict[str, Any]]:
    return await db_manager.get_note_by_id(note_id)

async def get_note_by_file_and_cat(filename: str, category: str) -> Optional[Dict[str, Any]]:
    return await db_manager.get_note_by_file_and_cat(filename, category)

async def get_all_tags_with_counts() -> List[Tuple[str, int]]:
    return await db_manager.get_all_tags_with_counts()

async def get_notes_by_tag(tag: str, limit: int = 50) -> List[Tuple[str, str]]:
    return await db_manager.get_notes_by_tag(tag, limit)


async def upsert_note_log(
    filename: str,
    category: str,
    tags: str,
    content: str,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
    commit: bool = True
) -> Tuple[bool, bool]:
    return await db_manager.upsert_note_log(filename, category, tags, content, etag, last_modified, commit)

async def note_exists(filename: str, category: str) -> bool:
    return await db_manager.note_exists(filename, category)

async def search_notes(query: str, limit: int = 50):
    return await db_manager.search_notes(query, limit)

async def search_notes_with_snippets(query: str, limit: int = 25) -> List[Tuple[str, str, str]]:
    return await db_manager.search_notes_with_snippets(query, limit)

async def get_total_notes_count() -> int:
    return await db_manager.get_total_notes_count()

async def search_rag_context(query: str, limit: int = 5) -> List[RAGNote]:
    return await db_manager.search_rag_context(query, limit)

async def delete_notes(file_cat_pairs: List[Tuple[str, str]], commit: bool = True) -> int:
    return await db_manager.delete_notes(file_cat_pairs, commit)

async def get_all_notes_metadata() -> Dict[Tuple[str, str], Dict[str, Any]]:
    return await db_manager.get_all_notes_metadata()

async def commit_db():
    await db_manager.commit()

async def get_today_notes():
    return await db_manager.get_today_notes()

async def get_recent_context(limit: int = 10):
    return await db_manager.get_recent_context(limit)

async def get_recent_notes_for_linking(limit: int = 10) -> str:
    return await db_manager.get_recent_notes_for_linking(limit)

async def close_db():
    await db_manager.close()