"""
test_timings.py - 阶段耗时聚合测试

运行：pytest tests/test_timings.py -v
"""

from __future__ import annotations

from webapp.timings import aggregate_timings

from webapp.models import TaskManager


def _task(status: str, **timings):
    return {"status": status, "timings": timings or None}


def test_empty_input():
    stats = aggregate_timings([])
    assert stats.sample_size == 0
    assert stats.completed == 0
    assert stats.cache_hit_rate == 0.0
    for stage in stats.stages.values():
        assert stage.samples == 0


def test_counts_statuses_even_without_timings():
    stats = aggregate_timings([_task("completed"), _task("failed"), _task("cancelled"), _task("pending")])
    assert stats.completed == 1
    assert stats.failed == 2  # failed + cancelled 都算未完成
    assert stats.sample_size == 4


def test_percentiles_and_average():
    rows = [_task("completed", solve=value) for value in (100, 200, 300, 400)]
    stats = aggregate_timings(rows)
    solve = stats.stages["solve"]
    assert solve.samples == 4
    assert solve.average == 250.0
    assert solve.p50 == 250.0
    # 线性插值：0.9 * (4-1) = 2.7 → 300*0.3 + 400*0.7
    assert solve.p90 == 370.0


def test_invalid_values_are_ignored():
    rows = [
        _task("completed", solve=120),
        {"status": "completed", "timings": {"solve": "not-a-number"}},
        {"status": "completed", "timings": {"solve": -5}},
        {"status": "completed", "timings": "{{ not json"},
        {"status": "completed"},
    ]
    stats = aggregate_timings(rows)
    assert stats.stages["solve"].samples == 1
    assert stats.stages["solve"].average == 120.0


def test_cache_hit_rate():
    rows = [
        {"status": "completed", "timings": {"total": 1000, "cached": ["ocr", "polish"]}},
        {"status": "completed", "timings": {"total": 2000, "cached": []}},
    ]
    stats = aggregate_timings(rows)
    assert stats.stages["ocr"].cache_hits == 1
    assert stats.stages["polish"].cache_hits == 1
    assert stats.cache_hit_rate == 1.0  # 2 次命中 / 2 个有 total 的样本


def test_json_string_timings_are_parsed():
    rows = [{"status": "completed", "timings_json": '{"total": 500, "solve": 400}'}]
    stats = aggregate_timings(rows)
    assert stats.stages["total"].samples == 1
    assert stats.stages["solve"].average == 400.0


def test_single_sample_percentile():
    stats = aggregate_timings([_task("completed", ocr=42)])
    assert stats.stages["ocr"].p50 == 42.0
    assert stats.stages["ocr"].p90 == 42.0


# ---------------------------------------------------------------------------
# TaskManager 阶段缓存与迁移
# ---------------------------------------------------------------------------


def test_stage_cache_roundtrip(tmp_path):
    manager = TaskManager(tmp_path / "tasks.db")
    manager.create_task("t1", 3)

    assert manager.get_cached_stage("t1", "ocr") is None
    manager.set_cached_stage("t1", "ocr", ["page one", "page two"])
    assert manager.get_cached_stage("t1", "ocr") == ["page one", "page two"]

    # 覆盖写入
    manager.set_cached_stage("t1", "ocr", ["updated"])
    assert manager.get_cached_stage("t1", "ocr") == ["updated"]

    manager.clear_stage_cache("t1")
    assert manager.get_cached_stage("t1", "ocr") is None


def test_update_task_rejects_unknown_column(tmp_path):
    manager = TaskManager(tmp_path / "tasks.db")
    manager.create_task("t1", 1)
    try:
        manager.update_task("t1", evil_column="x")
    except ValueError as exc:
        assert "evil_column" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当拒绝未知列名")


def test_timings_roundtrip_and_stats(tmp_path):
    manager = TaskManager(tmp_path / "tasks.db")
    manager.create_task("t1", 1)
    manager.update_task("t1", status="completed", timings_json='{"solve": 1200, "total": 1500}')

    task = manager.get_task("t1")
    assert task is not None
    assert task["timings"] == {"solve": 1200, "total": 1500}

    stats = aggregate_timings(manager.list_timings())
    assert stats.stages["solve"].samples == 1


def test_cleanup_old_tasks_also_drops_stage_cache(tmp_path):
    manager = TaskManager(tmp_path / "tasks.db")
    for i in range(5):
        manager.create_task(f"t{i}", 1)
        manager.set_cached_stage(f"t{i}", "ocr", [f"page-{i}"])

    removed = manager.cleanup_old_tasks(keep=2)
    remaining = manager.all_task_ids()
    assert len(remaining) == 2
    # 被删任务不能留下缓存记录
    for task_id in ("t0", "t1", "t2"):
        assert manager.get_cached_stage(task_id, "ocr") is None
    assert isinstance(removed, list)


def test_cleanup_by_age(tmp_path):
    manager = TaskManager(tmp_path / "tasks.db")
    manager.create_task("old", 1)
    manager.create_task("new", 1)

    # 把 old 的 created_at 改到很久以前
    import sqlite3

    with sqlite3.connect(str(manager.db_path)) as conn:
        conn.execute("UPDATE tasks SET created_at = 1.0 WHERE id = 'old'")
        conn.commit()

    manager.cleanup_old_tasks(keep=100, older_than=1000.0)
    assert manager.all_task_ids() == {"new"}


def test_migration_adds_columns_to_legacy_db(tmp_path):
    """模拟旧版数据库：只有基础列，验证迁移后仍可使用。"""
    import sqlite3

    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                num_images INTEGER NOT NULL DEFAULT 0,
                problem_type TEXT DEFAULT '',
                solver_provider TEXT DEFAULT '',
                solver_model TEXT DEFAULT '',
                solution_path TEXT DEFAULT '',
                filename TEXT DEFAULT '',
                error_message TEXT DEFAULT ''
            )
        """)
        conn.execute(
            "INSERT INTO tasks (id, status, created_at, updated_at, num_images) VALUES ('legacy', 'completed', 1.0, 1.0, 2)"
        )
        conn.commit()

    manager = TaskManager(db_path)
    task = manager.get_task("legacy")
    assert task is not None
    assert task["id"] == "legacy"
    assert task["timings"] is None

    # 新列可写
    manager.update_task("legacy", timings_json='{"total": 10}')
    assert manager.get_task("legacy")["timings"] == {"total": 10}
