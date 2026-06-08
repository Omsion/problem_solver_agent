"""webapp 数据模型 — 基于 sqlite3 的任务持久化管理器"""

import sqlite3
import time
from pathlib import Path


class TaskManager:
    """管理 SQLite 中的任务记录，提供 CRUD 和历史清理。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id              TEXT PRIMARY KEY,
                    status          TEXT NOT NULL DEFAULT 'pending',
                    created_at      REAL NOT NULL,
                    updated_at      REAL NOT NULL,
                    num_images      INTEGER NOT NULL DEFAULT 0,
                    problem_type    TEXT DEFAULT '',
                    solver_provider TEXT DEFAULT '',
                    solver_model    TEXT DEFAULT '',
                    solution_path   TEXT DEFAULT '',
                    filename        TEXT DEFAULT '',
                    error_message   TEXT DEFAULT ''
                )
            """)
            conn.commit()

    # ---- CRUD ----

    def create_task(self, task_id: str, num_images: int) -> None:
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO tasks (id, status, created_at, updated_at, num_images) "
                "VALUES (?, 'pending', ?, ?, ?)",
                (task_id, now, now, num_images),
            )
            conn.commit()

    def update_task(self, task_id: str, **kwargs) -> None:
        if not kwargs:
            return
        kwargs["updated_at"] = time.time()
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [task_id]
        with self._get_conn() as conn:
            conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
            conn.commit()

    def get_task(self, task_id: str) -> dict | None:
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row else None

    def get_recent_tasks(self, limit: int = 100) -> list[dict]:
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ---- 历史管理 ----

    def cleanup_old_tasks(self, keep: int = 100) -> list[str]:
        """删除超出保留数量的旧任务记录，返回需清理的 solution_path 列表。"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, solution_path FROM tasks ORDER BY created_at DESC"
            ).fetchall()
            if len(rows) <= keep:
                return []
            to_delete = rows[keep:]
            ids = [row[0] for row in to_delete]
            paths = [row[1] for row in to_delete if row[1]]
            placeholders = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM tasks WHERE id IN ({placeholders})", ids)
            conn.commit()
            return paths

    def delete_task(self, task_id: str) -> str | None:
        """删除指定任务，返回其 solution_path（如果有）。"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT solution_path FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            return row[0] if row and row[0] else None
