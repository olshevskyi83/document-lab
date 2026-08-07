from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from app.models import TaskRecord, TaskStatus, utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    catalog TEXT NOT NULL DEFAULT '',
    format TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'auto',
    ocr_languages TEXT NOT NULL DEFAULT 'auto',
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    duplicate_of TEXT,
    text_path TEXT,
    metadata_path TEXT,
    report_path TEXT,
    library_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    approved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_sha256 ON tasks(sha256);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
            if "library_path" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN library_path TEXT")
            connection.execute(
                "UPDATE tasks SET status=?, progress=0, error=? WHERE status=?",
                (TaskStatus.QUEUED, "Recovered after application restart", TaskStatus.PROCESSING),
            )

    def find_canonical_by_hash(self, sha256: str) -> TaskRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE sha256=? AND status IN (?, ?, ?) "
                "ORDER BY id LIMIT 1",
                (sha256, TaskStatus.QUEUED, TaskStatus.PROCESSING, TaskStatus.READY),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM tasks WHERE sha256=? AND status=? ORDER BY id LIMIT 1",
                    (sha256, TaskStatus.COMPLETED),
                ).fetchone()
        return TaskRecord.from_row(row) if row else None

    def is_known_hash(self, sha256: str) -> bool:
        known_statuses = (
            TaskStatus.QUEUED,
            TaskStatus.PROCESSING,
            TaskStatus.READY,
            TaskStatus.COMPLETED,
            TaskStatus.DUPLICATE,
        )
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT 1 FROM tasks WHERE sha256=? AND status IN ({','.join('?' for _ in known_statuses)}) LIMIT 1",
                (sha256, *known_statuses),
            ).fetchone()
        return row is not None

    def create_task(self, **values: object) -> TaskRecord:
        now = utc_now()
        columns = (
            "document_id", "sha256", "source_path", "source_filename", "relative_path",
            "catalog", "format", "content_type", "ocr_languages", "status", "progress",
            "duplicate_of", "library_path", "created_at", "updated_at",
        )
        params = tuple(values.get(column) for column in columns[:-2]) + (now, now)
        with self._lock, self.connect() as connection:
            cursor = connection.execute(
                f"INSERT INTO tasks ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                params,
            )
            row = connection.execute("SELECT * FROM tasks WHERE id=?", (cursor.lastrowid,)).fetchone()
        return TaskRecord.from_row(row)

    def list_tasks(self, limit: int = 200) -> list[TaskRecord]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [TaskRecord.from_row(row) for row in rows]

    def list_tasks_missing_library_path(self) -> list[TaskRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE library_path IS NULL ORDER BY id"
            ).fetchall()
        return [TaskRecord.from_row(row) for row in rows]

    def get_task(self, task_id: int) -> TaskRecord | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return TaskRecord.from_row(row) if row else None

    def claim_next_task(self) -> TaskRecord | None:
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM tasks WHERE status=? ORDER BY id LIMIT 1", (TaskStatus.QUEUED,)
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                "UPDATE tasks SET status=?, progress=5, error=NULL, updated_at=? WHERE id=?",
                (TaskStatus.PROCESSING, utc_now(), row["id"]),
            )
            connection.commit()
        return self.get_task(row["id"])

    def update_task(self, task_id: int, **values: object) -> None:
        if not values:
            return
        values["updated_at"] = utc_now()
        assignments = ",".join(f"{key}=?" for key in values)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE tasks SET {assignments} WHERE id=?", tuple(values.values()) + (task_id,)
            )

    def retry(self, task_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status=?, progress=0, error=NULL, updated_at=? "
                "WHERE id=? AND status=?",
                (TaskStatus.QUEUED, utc_now(), task_id, TaskStatus.FAILED),
            )
        return cursor.rowcount == 1

    def approve(self, task_id: int) -> bool:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status=?, progress=100, approved_at=?, updated_at=? "
                "WHERE id=? AND status=?",
                (TaskStatus.COMPLETED, now, now, task_id, TaskStatus.READY),
            )
        return cursor.rowcount == 1

    def delete_task_record(self, task_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM tasks WHERE id=? AND status!=?", (task_id, TaskStatus.PROCESSING)
            )
        return cursor.rowcount == 1
