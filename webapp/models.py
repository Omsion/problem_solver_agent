"""webapp 数据模型 — 基于 sqlite3 的任务持久化管理器"""

import json
import sqlite3
import time
from pathlib import Path

# 任务表需要的列及其 DDL 片段（用于幂等迁移：旧库只有前面几列）
_TASK_COLUMNS: dict[str, str] = {
    "timings_json": "TEXT DEFAULT ''",
    "answer_card": "TEXT DEFAULT ''",
    "verified": "INTEGER NOT NULL DEFAULT 0",
    # 多用户隔离：任务归属。旧库补列时默认给内置本地用户，
    # 这样升级后既有任务仍然可见（归属到本地账号）。
    "user_id": "TEXT NOT NULL DEFAULT 'local'",
    "tenant_id": "TEXT NOT NULL DEFAULT 'default'",
}

# 合法任务状态（用于校验与文档化）
TASK_STATUSES = ("pending", "processing", "completed", "failed", "cancelled")


class TaskManager:
    """管理 SQLite 中的任务记录，提供 CRUD 和历史清理。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        # 多个后台线程会并发写库（流水线进度 + 阶段缓存），
        # 设置 busy_timeout 避免立刻抛 "database is locked"。
        conn.execute("PRAGMA busy_timeout=5000")
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
            self._migrate_tasks(conn)
            # 列表查询按创建时间倒序，加索引避免全表扫描
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks (created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status)")
            # 多用户隔离后，最常见的查询是"某用户的任务，按时间倒序"
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_time ON tasks (user_id, created_at DESC)")
            # 阶段缓存：重试时复用分类/OCR 结果，避免重复付费
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stage_cache (
                    task_id     TEXT NOT NULL,
                    stage       TEXT NOT NULL,
                    payload     TEXT NOT NULL,
                    created_at  REAL NOT NULL,
                    PRIMARY KEY (task_id, stage)
                )
            """)
            conn.commit()

    def _migrate_tasks(self, conn: sqlite3.Connection) -> None:
        """幂等地补齐缺失列，保证既有 tasks.db 可以继续使用。"""
        existing = {
            row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        for column, ddl in _TASK_COLUMNS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {ddl}")

    # ---- CRUD ----

    def create_task(
        self,
        task_id: str,
        num_images: int,
        *,
        user_id: str = "local",
        tenant_id: str = "default",
    ) -> None:
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO tasks "
                "(id, status, created_at, updated_at, num_images, user_id, tenant_id) "
                "VALUES (?, 'pending', ?, ?, ?, ?, ?)",
                (task_id, now, now, num_images, user_id, tenant_id),
            )
            conn.commit()

    def update_task(self, task_id: str, **kwargs) -> None:
        if not kwargs:
            return
        # 显式拒绝未知字段，避免 SQL 注入与静默拼错列名
        allowed = {
            "status",
            "problem_type",
            "solver_provider",
            "solver_model",
            "solution_path",
            "filename",
            "error_message",
            "timings_json",
            "answer_card",
            "verified",
        }
        unknown = set(kwargs) - allowed
        if unknown:
            raise ValueError(f"未知的任务字段: {', '.join(sorted(unknown))}")
        kwargs["updated_at"] = time.time()
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [task_id]
        with self._get_conn() as conn:
            conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
            conn.commit()

    def get_task(self, task_id: str, *, user_id: str | None = None) -> dict | None:
        """按 id 取任务。

        Args:
            user_id: 传入时校验归属，不属于该用户则返回 None
                （不抛异常，调用方统一按"任务不存在"处理，避免泄露存在性）。
        """
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            if user_id is None:
                row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id)
                ).fetchone()
            return _row_to_task(row) if row else None

    def owns_task(self, task_id: str, user_id: str) -> bool:
        """判断任务是否属于该用户（管理员场景可跳过）。"""
        return self.get_task(task_id, user_id=user_id) is not None

    def get_recent_tasks(
        self, limit: int = 100, *, user_id: str | None = None, tenant_id: str | None = None
    ) -> list[dict]:
        """按时间倒序列出任务。

        Args:
            user_id: 传入时只返回该用户的任务（多用户隔离）
            tenant_id: 传入时按租户过滤
        """
        clauses: list[str] = []
        params: list[object] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)

        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ?", params
            ).fetchall()
            return [_row_to_task(r) for r in rows]

    def all_task_ids(self) -> set[str]:
        with self._get_conn() as conn:
            return {row[0] for row in conn.execute("SELECT id FROM tasks").fetchall()}

    def list_timings(self, limit: int = 50, *, user_id: str | None = None) -> list[dict]:
        """只取状态与耗时，供 /api/stats 聚合使用。"""
        if user_id is None:
            query = "SELECT status, timings_json FROM tasks ORDER BY created_at DESC LIMIT ?"
            params: list[object] = [limit]
        else:
            query = (
                "SELECT status, timings_json FROM tasks WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ?"
            )
            params = [user_id, limit]
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
            result = []
            for row in rows:
                timings: dict | None = None
                raw = row["timings_json"]
                if raw:
                    try:
                        timings = json.loads(raw)
                    except (ValueError, TypeError):
                        timings = None
                result.append({"status": row["status"], "timings": timings})
            return result

    # ---- 阶段缓存（重试时复用）----

    def set_cached_stage(self, task_id: str, stage: str, payload: object) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO stage_cache (task_id, stage, payload, created_at) "
                "VALUES (?, ?, ?, ?)",
                (task_id, stage, json.dumps(payload, ensure_ascii=False), time.time()),
            )
            conn.commit()

    def get_cached_stage(self, task_id: str, stage: str) -> object | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT payload FROM stage_cache WHERE task_id = ? AND stage = ?",
                (task_id, stage),
            ).fetchone()
            if not row:
                return None
            try:
                return json.loads(row[0])
            except (ValueError, TypeError):
                return None

    def clear_stage_cache(self, task_id: str) -> None:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM stage_cache WHERE task_id = ?", (task_id,))
            conn.commit()

    # ---- 历史管理 ----

    def cleanup_old_tasks(self, keep: int = 100, older_than: float = 0.0) -> list[str]:
        """删除超出保留数量或超过保留时间的任务记录。

        Args:
            keep: 最多保留的任务数（按创建时间倒序）
            older_than: 早于该时间戳（秒）的任务也一并删除；0 表示不按时间清理

        Returns:
            被删除任务的 solution_path 列表（供调用方删除文件）
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, solution_path, created_at FROM tasks ORDER BY created_at DESC"
            ).fetchall()
            to_delete: list[tuple[str, str]] = []
            for index, row in enumerate(rows):
                over_count = index >= keep
                too_old = bool(older_than) and row[2] < older_than
                if over_count or too_old:
                    to_delete.append((row[0], row[1] or ""))

            if not to_delete:
                return []

            ids = [task_id for task_id, _ in to_delete]
            placeholders = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM tasks WHERE id IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM stage_cache WHERE task_id IN ({placeholders})", ids)
            conn.commit()
            return [path for _, path in to_delete if path]

    def delete_task(self, task_id: str, *, user_id: str | None = None) -> str | None:
        """删除指定任务（含其阶段缓存），返回其 solution_path（如果有）。

        Args:
            user_id: 传入时只允许删除该用户自己的任务；不属于则返回 None
                （与"不存在"同义，避免通过响应区分任务是否存在）。
        """
        with self._get_conn() as conn:
            if user_id is None:
                row = conn.execute(
                    "SELECT solution_path FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    return None
                conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            else:
                row = conn.execute(
                    "SELECT solution_path FROM tasks WHERE id = ? AND user_id = ?",
                    (task_id, user_id),
                ).fetchone()
                if row is None:
                    return None
                conn.execute(
                    "DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id)
                )
            conn.execute("DELETE FROM stage_cache WHERE task_id = ?", (task_id,))
            conn.commit()
            return row[0] if row and row[0] else None


def _row_to_task(row: sqlite3.Row) -> dict:
    """把数据库行转成字典，并把 timings_json 解析成 timings 字段。"""
    task = dict(row)
    raw = task.pop("timings_json", "") or ""
    timings = None
    if raw:
        try:
            timings = json.loads(raw)
        except (ValueError, TypeError):
            timings = None
    task["timings"] = timings
    return task
