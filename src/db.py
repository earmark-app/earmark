from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 5

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    name TEXT NOT NULL,
    hardcover_token TEXT NOT NULL,
    hardcover_user_id INTEGER,
    hardcover_username TEXT,
    abs_user_id TEXT,
    needs_token_refresh INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sync_rules (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    direction TEXT NOT NULL CHECK (direction IN ('hc_to_abs', 'abs_to_hc', 'bidirectional')),
    hc_status_id INTEGER,
    hc_list_id INTEGER,
    abs_target_type TEXT NOT NULL CHECK (abs_target_type IN ('collection', 'playlist')),
    abs_target_name TEXT NOT NULL,
    abs_target_id TEXT,
    abs_library_id TEXT NOT NULL,
    remove_stale INTEGER DEFAULT 1,
    enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS book_mappings (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    hardcover_book_id INTEGER NOT NULL,
    hardcover_edition_id INTEGER,
    abs_library_item_id TEXT NOT NULL,
    match_method TEXT NOT NULL,
    match_confidence REAL DEFAULT 1.0,
    title TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, hardcover_book_id, abs_library_item_id)
);

CREATE TABLE IF NOT EXISTS sync_state (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    rule_id TEXT NOT NULL REFERENCES sync_rules(id) ON DELETE CASCADE,
    book_mapping_id TEXT NOT NULL REFERENCES book_mappings(id) ON DELETE CASCADE,
    last_synced_at TEXT DEFAULT (datetime('now')),
    sync_direction TEXT NOT NULL,
    UNIQUE(rule_id, book_mapping_id)
);

CREATE TABLE IF NOT EXISTS progress_state (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    abs_library_item_id TEXT NOT NULL,
    hardcover_book_id INTEGER NOT NULL,
    last_abs_progress REAL,
    last_abs_is_finished INTEGER DEFAULT 0,
    last_hc_status_id INTEGER,
    last_synced_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, abs_library_item_id)
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    rule_id TEXT REFERENCES sync_rules(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    direction TEXT,
    details TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sync_log_user ON sync_log(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sync_log_action ON sync_log(action, created_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

DEFAULT_SETTINGS = {
    "sync_interval": "*/15 * * * *",
    "dry_run": "false",
    "log_retention_days": "30",
    "fuzzy_match_threshold": "0.85",
    "schema_version": "1",
    "sync_ratings_to_abs_tags": "false",
    "abs_url": "",
    "abs_api_key": "",
}

# ---------------------------------------------------------------------------
# Migrations (additive only: ADD COLUMN, CREATE TABLE)
# ---------------------------------------------------------------------------

MIGRATIONS = {
    2: [
        "ALTER TABLE progress_state ADD COLUMN last_hc_to_abs_status_id INTEGER",
        "ALTER TABLE progress_state ADD COLUMN last_hc_to_abs_synced_at TEXT",
    ],
    3: [
        """CREATE TABLE IF NOT EXISTS book_ratings (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            hardcover_book_id INTEGER NOT NULL,
            abs_library_item_id TEXT,
            rating REAL,
            source TEXT DEFAULT 'hardcover',
            synced_to_abs INTEGER DEFAULT 0,
            last_synced_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, hardcover_book_id)
        )""",
    ],
    4: [
        """CREATE TABLE IF NOT EXISTS reading_dates (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            hardcover_book_id INTEGER NOT NULL,
            abs_library_item_id TEXT,
            date_started TEXT,
            date_finished TEXT,
            source_started TEXT,
            source_finished TEXT,
            last_synced_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, hardcover_book_id)
        )""",
    ],
    # v5: Move ABS credentials from per-user to global settings.
    # Data migration handled in _apply_migrations before DROP COLUMN.
    5: [
        "ALTER TABLE users DROP COLUMN abs_url",
        "ALTER TABLE users DROP COLUMN abs_api_key",
        "ALTER TABLE users DROP COLUMN abs_username",
        "ALTER TABLE users DROP COLUMN abs_library_ids",
        "ALTER TABLE users DROP COLUMN abs_is_admin",
    ],
}


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def connect(self):
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self):
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            # Pre-populate default settings
            for key, value in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, value),
                )
        self._apply_migrations()

    def _apply_migrations(self):
        """Run incremental schema migrations from current version to SCHEMA_VERSION."""
        current = int(self.get_setting("schema_version") or "1")
        if current >= SCHEMA_VERSION:
            return
        logger.info("Migrating database from v%d to v%d", current, SCHEMA_VERSION)
        with self.connect() as conn:
            for version in range(current + 1, SCHEMA_VERSION + 1):
                # v5 data migration: preserve ABS credentials before dropping columns
                if version == 5:
                    try:
                        row = conn.execute(
                            "SELECT abs_url, abs_api_key FROM users "
                            "WHERE abs_url IS NOT NULL AND abs_url != '' LIMIT 1"
                        ).fetchone()
                        if row:
                            conn.execute(
                                "INSERT INTO settings (key, value, updated_at) VALUES ('abs_url', ?, datetime('now')) "
                                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                                (row[0],),
                            )
                            conn.execute(
                                "INSERT INTO settings (key, value, updated_at) VALUES ('abs_api_key', ?, datetime('now')) "
                                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                                (row[1],),
                            )
                            logger.info("Migrated ABS credentials from first user to global settings")
                    except Exception as e:
                        if "no such column" not in str(e).lower():
                            raise
                        logger.debug("v5 data migration skipped (columns already dropped): %s", e)

                stmts = MIGRATIONS.get(version, [])
                for stmt in stmts:
                    try:
                        conn.execute(stmt)
                    except Exception as e:
                        # Column/table may already exist or already be dropped
                        if "duplicate column" in str(e).lower() or "already exists" in str(e).lower() or "no such column" in str(e).lower():
                            logger.debug("Migration v%d: %s (already applied)", version, e)
                        else:
                            raise
                logger.info("Applied migration v%d (%d statements)", version, len(stmts))
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES ('schema_version', ?, datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (str(SCHEMA_VERSION),),
            )

    # --- Helpers ---

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _gen_id() -> str:
        import os
        return os.urandom(8).hex()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        return dict(row)

    # --- Users ---

    def create_user(self, data: dict) -> dict:
        user_id = self._gen_id()
        now = self._now()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO users (id, name, hardcover_token, abs_user_id,
                   enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, data["name"], data["hardcover_token"],
                 data.get("abs_user_id"),
                 int(data.get("enabled", True)), now, now),
            )
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                return None
            d = self._row_to_dict(row)
            d["enabled"] = bool(d.get("enabled", 1))
            d["needs_token_refresh"] = bool(d.get("needs_token_refresh", 0))
            return d

    def list_users(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
            results = []
            for row in rows:
                d = self._row_to_dict(row)
                d["enabled"] = bool(d.get("enabled", 1))
                d["needs_token_refresh"] = bool(d.get("needs_token_refresh", 0))
                results.append(d)
            return results

    def update_user(self, user_id: str, data: dict) -> Optional[dict]:
        fields = []
        values = []
        for key in ("name", "hardcover_token",
                     "hardcover_user_id", "hardcover_username",
                     "abs_user_id", "needs_token_refresh", "enabled"):
            if key in data and data[key] is not None:
                if key in ("enabled", "needs_token_refresh"):
                    fields.append(f"{key} = ?")
                    values.append(int(data[key]))
                else:
                    fields.append(f"{key} = ?")
                    values.append(data[key])
        if not fields:
            return self.get_user(user_id)
        fields.append("updated_at = ?")
        values.append(self._now())
        values.append(user_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values
            )
        return self.get_user(user_id)

    def delete_user(self, user_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return cursor.rowcount > 0

    # --- Sync Rules ---

    def create_sync_rule(self, data: dict) -> dict:
        rule_id = self._gen_id()
        now = self._now()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO sync_rules (id, user_id, direction, hc_status_id, hc_list_id,
                   abs_target_type, abs_target_name, abs_library_id, remove_stale, enabled,
                   created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rule_id, data["user_id"], data["direction"],
                 data.get("hc_status_id"), data.get("hc_list_id"),
                 data["abs_target_type"], data["abs_target_name"],
                 data["abs_library_id"], int(data.get("remove_stale", True)),
                 int(data.get("enabled", True)), now, now),
            )
        return self.get_sync_rule(rule_id)

    def get_sync_rule(self, rule_id: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM sync_rules WHERE id = ?", (rule_id,)).fetchone()
            if row is None:
                return None
            d = self._row_to_dict(row)
            d["enabled"] = bool(d.get("enabled", 1))
            d["remove_stale"] = bool(d.get("remove_stale", 1))
            return d

    def list_sync_rules(self, user_id: Optional[str] = None) -> list[dict]:
        with self.connect() as conn:
            if user_id:
                rows = conn.execute(
                    "SELECT * FROM sync_rules WHERE user_id = ? ORDER BY created_at",
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sync_rules ORDER BY created_at"
                ).fetchall()
            results = []
            for row in rows:
                d = self._row_to_dict(row)
                d["enabled"] = bool(d.get("enabled", 1))
                d["remove_stale"] = bool(d.get("remove_stale", 1))
                results.append(d)
            return results

    def update_sync_rule(self, rule_id: str, data: dict) -> Optional[dict]:
        fields = []
        values = []
        for key in ("direction", "hc_status_id", "hc_list_id", "abs_target_type",
                     "abs_target_name", "abs_target_id", "abs_library_id"):
            if key in data and data[key] is not None:
                fields.append(f"{key} = ?")
                values.append(data[key])
        for key in ("remove_stale", "enabled"):
            if key in data and data[key] is not None:
                fields.append(f"{key} = ?")
                values.append(int(data[key]))
        if not fields:
            return self.get_sync_rule(rule_id)
        fields.append("updated_at = ?")
        values.append(self._now())
        values.append(rule_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE sync_rules SET {', '.join(fields)} WHERE id = ?", values
            )
        return self.get_sync_rule(rule_id)

    def delete_sync_rule(self, rule_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM sync_rules WHERE id = ?", (rule_id,))
            return cursor.rowcount > 0

    # --- Book Mappings ---

    def create_book_mapping(self, data: dict) -> dict:
        mapping_id = self._gen_id()
        now = self._now()
        with self.connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO book_mappings
                   (id, user_id, hardcover_book_id, hardcover_edition_id,
                    abs_library_item_id, match_method, match_confidence, title, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (mapping_id, data["user_id"], data["hardcover_book_id"],
                 data.get("hardcover_edition_id"), data["abs_library_item_id"],
                 data["match_method"], data.get("match_confidence", 1.0),
                 data.get("title"), now),
            )
        return self.get_book_mapping(mapping_id)

    def get_book_mapping(self, mapping_id: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM book_mappings WHERE id = ?", (mapping_id,)).fetchone()
            return self._row_to_dict(row) if row else None

    def get_book_mapping_by_books(
        self, user_id: str, hardcover_book_id: int, abs_library_item_id: str
    ) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM book_mappings
                   WHERE user_id = ? AND hardcover_book_id = ? AND abs_library_item_id = ?""",
                (user_id, hardcover_book_id, abs_library_item_id),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def find_mapping_by_hc_book(self, user_id: str, hardcover_book_id: int) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM book_mappings WHERE user_id = ? AND hardcover_book_id = ?",
                (user_id, hardcover_book_id),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def find_mapping_by_abs_item(self, user_id: str, abs_library_item_id: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM book_mappings WHERE user_id = ? AND abs_library_item_id = ?",
                (user_id, abs_library_item_id),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def list_book_mappings(
        self, user_id: Optional[str] = None, method: Optional[str] = None
    ) -> list[dict]:
        query = "SELECT * FROM book_mappings WHERE 1=1"
        params: list[Any] = []
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if method:
            query += " AND match_method = ?"
            params.append(method)
        query += " ORDER BY created_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def delete_book_mapping(self, mapping_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM book_mappings WHERE id = ?", (mapping_id,))
            return cursor.rowcount > 0

    # --- Sync State ---

    def upsert_sync_state(self, rule_id: str, book_mapping_id: str, direction: str):
        state_id = self._gen_id()
        now = self._now()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO sync_state (id, rule_id, book_mapping_id, last_synced_at, sync_direction)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(rule_id, book_mapping_id) DO UPDATE SET
                   last_synced_at = excluded.last_synced_at,
                   sync_direction = excluded.sync_direction""",
                (state_id, rule_id, book_mapping_id, now, direction),
            )

    def get_sync_state_for_rule(self, rule_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sync_state WHERE rule_id = ?", (rule_id,)
            ).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def count_sync_state_for_rule(self, rule_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM sync_state WHERE rule_id = ?", (rule_id,)
            ).fetchone()
            return row["cnt"] if row else 0

    def delete_sync_state(self, rule_id: str, book_mapping_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM sync_state WHERE rule_id = ? AND book_mapping_id = ?",
                (rule_id, book_mapping_id),
            )
            return cursor.rowcount > 0

    # --- Progress State ---

    def upsert_progress_state(
        self,
        user_id: str,
        abs_library_item_id: str,
        hardcover_book_id: int,
        progress: float,
        is_finished: bool,
        hc_status_id: Optional[int] = None,
    ):
        state_id = self._gen_id()
        now = self._now()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO progress_state
                   (id, user_id, abs_library_item_id, hardcover_book_id,
                    last_abs_progress, last_abs_is_finished, last_hc_status_id, last_synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, abs_library_item_id) DO UPDATE SET
                   last_abs_progress = excluded.last_abs_progress,
                   last_abs_is_finished = excluded.last_abs_is_finished,
                   last_hc_status_id = excluded.last_hc_status_id,
                   last_synced_at = excluded.last_synced_at""",
                (state_id, user_id, abs_library_item_id, hardcover_book_id,
                 progress, int(is_finished), hc_status_id, now),
            )

    def get_progress_state(self, user_id: str, abs_library_item_id: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM progress_state
                   WHERE user_id = ? AND abs_library_item_id = ?""",
                (user_id, abs_library_item_id),
            ).fetchone()
            if row is None:
                return None
            d = self._row_to_dict(row)
            d["last_abs_is_finished"] = bool(d.get("last_abs_is_finished", 0))
            return d

    # --- Sync Log ---

    def add_sync_log(
        self,
        action: str,
        user_id: Optional[str] = None,
        rule_id: Optional[str] = None,
        direction: Optional[str] = None,
        details: Optional[dict] = None,
    ):
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO sync_log (user_id, rule_id, action, direction, details)
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, rule_id, action, direction,
                 json.dumps(details) if details else None),
            )

    def list_sync_log(
        self,
        user_id: Optional[str] = None,
        action: Optional[str] = None,
        direction: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        query = "SELECT * FROM sync_log WHERE 1=1"
        count_query = "SELECT COUNT(*) as cnt FROM sync_log WHERE 1=1"
        params: list[Any] = []
        if user_id:
            query += " AND user_id = ?"
            count_query += " AND user_id = ?"
            params.append(user_id)
        if action:
            query += " AND action = ?"
            count_query += " AND action = ?"
            params.append(action)
        if direction:
            query += " AND direction = ?"
            count_query += " AND direction = ?"
            params.append(direction)
        with self.connect() as conn:
            total_row = conn.execute(count_query, params).fetchone()
            total = total_row["cnt"] if total_row else 0
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            rows = conn.execute(query, [*params, limit, offset]).fetchall()
            return [self._row_to_dict(row) for row in rows], total

    def delete_sync_log(self, before_date: Optional[str] = None) -> int:
        with self.connect() as conn:
            if before_date:
                cursor = conn.execute(
                    "DELETE FROM sync_log WHERE created_at < ?", (before_date,)
                )
            else:
                cursor = conn.execute("DELETE FROM sync_log")
            return cursor.rowcount

    def get_last_sync_log(self) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sync_log ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return self._row_to_dict(row) if row else None

    # --- Settings ---

    def get_setting(self, key: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None

    def get_all_settings(self) -> dict[str, str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            return {row["key"]: row["value"] for row in rows}

    def update_setting(self, key: str, value: str):
        now = self._now()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (key, value, now),
            )

    def update_settings(self, settings: dict[str, str]):
        for key, value in settings.items():
            self.update_setting(key, value)

    # --- Book Ratings ---

    def upsert_book_rating(
        self,
        user_id: str,
        hardcover_book_id: int,
        rating: float,
        source: str = "hardcover",
        abs_library_item_id: Optional[str] = None,
    ) -> dict:
        rating_id = self._gen_id()
        now = self._now()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO book_ratings
                   (id, user_id, hardcover_book_id, abs_library_item_id, rating, source, last_synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, hardcover_book_id) DO UPDATE SET
                   rating = excluded.rating,
                   abs_library_item_id = COALESCE(excluded.abs_library_item_id, abs_library_item_id),
                   source = excluded.source,
                   last_synced_at = excluded.last_synced_at""",
                (rating_id, user_id, hardcover_book_id, abs_library_item_id, rating, source, now),
            )
        return self.get_book_rating(user_id, hardcover_book_id)

    def get_book_rating(self, user_id: str, hardcover_book_id: int) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM book_ratings WHERE user_id = ? AND hardcover_book_id = ?",
                (user_id, hardcover_book_id),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def list_book_ratings(self, user_id: Optional[str] = None) -> list[dict]:
        query = "SELECT * FROM book_ratings WHERE 1=1"
        params: list[Any] = []
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        query += " ORDER BY last_synced_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def mark_rating_synced_to_abs(self, user_id: str, hardcover_book_id: int):
        with self.connect() as conn:
            conn.execute(
                "UPDATE book_ratings SET synced_to_abs = 1 WHERE user_id = ? AND hardcover_book_id = ?",
                (user_id, hardcover_book_id),
            )

    # --- Reading Dates ---

    def upsert_reading_dates(
        self,
        user_id: str,
        hardcover_book_id: int,
        abs_library_item_id: Optional[str] = None,
        date_started: Optional[str] = None,
        date_finished: Optional[str] = None,
        source_started: Optional[str] = None,
        source_finished: Optional[str] = None,
    ) -> dict:
        date_id = self._gen_id()
        now = self._now()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO reading_dates
                   (id, user_id, hardcover_book_id, abs_library_item_id,
                    date_started, date_finished, source_started, source_finished, last_synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, hardcover_book_id) DO UPDATE SET
                   abs_library_item_id = COALESCE(excluded.abs_library_item_id, abs_library_item_id),
                   date_started = CASE
                       WHEN excluded.date_started IS NOT NULL AND (date_started IS NULL OR excluded.date_started < date_started)
                       THEN excluded.date_started ELSE date_started END,
                   date_finished = CASE
                       WHEN excluded.date_finished IS NOT NULL AND (date_finished IS NULL OR excluded.date_finished > date_finished)
                       THEN excluded.date_finished ELSE date_finished END,
                   source_started = CASE
                       WHEN excluded.date_started IS NOT NULL AND (date_started IS NULL OR excluded.date_started < date_started)
                       THEN excluded.source_started ELSE source_started END,
                   source_finished = CASE
                       WHEN excluded.date_finished IS NOT NULL AND (date_finished IS NULL OR excluded.date_finished > date_finished)
                       THEN excluded.source_finished ELSE source_finished END,
                   last_synced_at = excluded.last_synced_at""",
                (date_id, user_id, hardcover_book_id, abs_library_item_id,
                 date_started, date_finished, source_started, source_finished, now),
            )
        return self.get_reading_dates(user_id, hardcover_book_id)

    def get_reading_dates(self, user_id: str, hardcover_book_id: int) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM reading_dates WHERE user_id = ? AND hardcover_book_id = ?",
                (user_id, hardcover_book_id),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def list_reading_dates(self, user_id: Optional[str] = None) -> list[dict]:
        query = "SELECT * FROM reading_dates WHERE 1=1"
        params: list[Any] = []
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        query += " ORDER BY last_synced_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_dict(row) for row in rows]
