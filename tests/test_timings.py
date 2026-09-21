"""
test_timings.py - 阶段耗时聚合测试

运行：pytest tests/test_timings.py -v
"""

from __future__ import annotations

import pytest

from webapp.timings import aggregate_timings

from webapp.models import _TASK_COLUMNS, TaskManager

# 转录双层落盘新增的三列（组 I）；用契约里的真实列名做断言，
# 避免测试与实现各自写错字面量却互相"通过"。
_TRANSCRIPT_COLUMNS = ("problem_text", "ocr_raw_text", "vision_mode")


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


def test_retention_pruning_removes_ocr_archives(tmp_path, monkeypatch):
    """F6：保留策略剪掉的任务，其 OCR 归档也要一起删（否则文件永久残留）。

    归档写于任务创建那天，清理发生在之后任意一天 —— 所以删除必须按日期目录
    glob，不能拼"今天"。
    """
    import sqlite3

    from problem_solver_agent import config as core_config
    from problem_solver_agent.utils import sanitize_filename
    from webapp import config as web_config
    from webapp.pipeline import PipelineService

    manager = TaskManager(tmp_path / "tasks.db")
    service = PipelineService(tmp_path / "solutions", manager)
    monkeypatch.setattr(web_config, "UPLOAD_DIR", tmp_path / "uploads", raising=False)
    monkeypatch.setattr(core_config, "TASK_RETENTION_COUNT", 1, raising=False)
    monkeypatch.setattr(core_config, "TASK_RETENTION_DAYS", 0, raising=False)

    archives = {}
    for task_id in ("old", "new"):
        manager.create_task(task_id, 1)
        archive = core_config.OCR_DIR / "2026-01-01" / f"{sanitize_filename(task_id)}.md"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text("原始 OCR", encoding="utf-8")
        archives[task_id] = archive
    # 明确 created_at，避免两条记录时间戳相同导致"谁被剪掉"不确定
    with sqlite3.connect(str(manager.db_path)) as conn:
        conn.execute("UPDATE tasks SET created_at = 1.0 WHERE id = 'old'")
        conn.execute("UPDATE tasks SET created_at = 2.0 WHERE id = 'new'")
        conn.commit()

    service._cleanup_old()  # noqa: SLF001 - 保留策略是私有入口，测试直接驱动

    assert manager.all_task_ids() == {"new"}
    assert not archives["old"].exists()
    assert archives["new"].exists()


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


# ---------------------------------------------------------------------------
# 转录落库与历史搜索（组 I）
# ---------------------------------------------------------------------------


def test_transcript_columns_are_declared_and_migrated_idempotently(tmp_path):
    """三列必须进 _TASK_COLUMNS，且重开旧库时被幂等补上。"""
    for column in _TRANSCRIPT_COLUMNS:
        assert column in _TASK_COLUMNS, f"_TASK_COLUMNS 缺少 {column}"

    db_path = tmp_path / "tasks.db"
    manager = TaskManager(db_path)
    # 旧库场景再走一遍初始化：补列必须幂等（重复 ALTER 会抛 duplicate column）
    TaskManager(db_path)

    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    for column in _TRANSCRIPT_COLUMNS:
        assert column in existing


def test_update_task_accepts_transcript_columns(tmp_path):
    """update_task 白名单必须同步加这三列，否则流水线写库时会抛未知字段。"""
    manager = TaskManager(tmp_path / "tasks.db")
    manager.create_task("t1", 2)

    manager.update_task(
        "t1",
        problem_text="求下列极限",
        ocr_raw_text="第 1 页原始识别文本",
        vision_mode="combined",
    )

    task = manager.get_task("t1")
    assert task is not None
    assert task["problem_text"] == "求下列极限"
    assert task["ocr_raw_text"] == "第 1 页原始识别文本"
    assert task["vision_mode"] == "combined"


def test_search_tasks_hits_ocr_raw_text_and_filename(tmp_path):
    """搜索要能命中被润色改写的原始 OCR 关键词，以及文件名。"""
    manager = TaskManager(tmp_path / "tasks.db")
    manager.create_task("t1", 1)
    manager.create_task("t2", 1)

    manager.update_task(
        "t1",
        ocr_raw_text="原始识别里的关键词 洛必达",
        problem_text="润色后的题干",
        filename="20260101-aaa.md",
    )
    manager.update_task("t2", problem_text="另一道题", filename="20260101-bbb.md")

    # 命中 ocr_raw_text
    hits = manager.search_tasks("洛必达")
    assert [t["id"] for t in hits] == ["t1"]
    # 命中 filename
    assert [t["id"] for t in manager.search_tasks("bbb")] == ["t2"]
    # 命中 problem_text
    assert [t["id"] for t in manager.search_tasks("润色后")] == ["t1"]
    # 无命中
    assert manager.search_tasks("不存在的关键词") == []
    # 空查询直接返回空，不能退化成"列出全部"
    assert manager.search_tasks("") == []


def test_search_tasks_respects_user_and_limit(tmp_path):
    """搜索必须保留多用户隔离语义（普通用户搜不到别人的任务）。"""
    manager = TaskManager(tmp_path / "tasks.db")
    manager.create_task("mine", 1, user_id="u1")
    manager.create_task("theirs", 1, user_id="u2")
    manager.update_task("mine", problem_text="共享关键词 alpha")
    manager.update_task("theirs", problem_text="共享关键词 alpha")

    assert {t["id"] for t in manager.search_tasks("alpha")} == {"mine", "theirs"}
    assert [t["id"] for t in manager.search_tasks("alpha", user_id="u1")] == ["mine"]
    assert [t["id"] for t in manager.search_tasks("alpha", user_id="u1", limit=1)] == ["mine"]


def test_get_recent_tasks_still_works_alongside_search(tmp_path):
    """迁移后原有列表查询不能回归。"""
    manager = TaskManager(tmp_path / "tasks.db")
    for index in range(3):
        manager.create_task(f"t{index}", 1)
        manager.update_task(f"t{index}", problem_text=f"题目 {index}")

    recent = manager.get_recent_tasks(limit=10)
    assert len(recent) == 3
    # 倒序：最后创建的最靠前（created_at 相同则顺序不保证，只断言集合与字段）
    assert {t["id"] for t in recent} == {"t0", "t1", "t2"}
    for task in recent:
        assert "problem_text" in task and "ocr_raw_text" in task and "vision_mode" in task
    assert manager.get_recent_tasks(limit=2, user_id="local") != []


def test_pipeline_persists_transcript_fields(tmp_path, monkeypatch):
    """流水线必须把 core 返回的三个转录字段写进库（完成分支 + 取消分支）。

    取消分支尤其重要：OCR 在视觉阶段就已完成，取消只影响求解，
    此时转录内容已经产生，不写就等于白付了视觉调用的钱。
    """
    from webapp.pipeline import PipelineService
    from problem_solver_agent.core_pipeline import SolutionPipeline

    manager = TaskManager(tmp_path / "tasks.db")
    service = PipelineService(tmp_path / "solutions", manager)

    def _fake_run(result):
        def _run(self, task_id, image_paths, **kwargs):
            return result

        return _run

    # --- 完成分支 ---
    answer = tmp_path / "solutions" / "done.md"
    answer.parent.mkdir(parents=True, exist_ok=True)
    answer.write_text("# 解答\n\n选 B。\n", encoding="utf-8")
    monkeypatch.setattr(
        SolutionPipeline,
        "run",
        _fake_run(
            {
                "status": "completed",
                "path": answer,
                "timings": {"total": 1000},
                "answer_card": {"text": "选 B"},
                "problem_type": "GENERAL",
                "problem_text": "润色后的题目",
                "ocr_raw_text": "原始识别文本",
                "vision_mode": "combined",
            }
        ),
    )
    manager.create_task("done", 1)
    service.run("done", [], lambda event: None)

    task = manager.get_task("done")
    assert task["status"] == "completed"
    assert task["problem_text"] == "润色后的题目"
    assert task["ocr_raw_text"] == "原始识别文本"
    assert task["vision_mode"] == "combined"

    # --- 取消分支：能写多少写多少 ---
    monkeypatch.setattr(
        SolutionPipeline,
        "run",
        _fake_run(
            {
                "status": "cancelled",
                "path": None,
                "timings": {},
                "problem_text": "被取消的题目",
                "ocr_raw_text": "被取消的原始 OCR",
                "vision_mode": "parallel",
            }
        ),
    )
    manager.create_task("cancelled", 1)
    service.run("cancelled", [], lambda event: None)

    cancelled = manager.get_task("cancelled")
    assert cancelled["status"] == "cancelled"
    assert cancelled["problem_text"] == "被取消的题目"
    assert cancelled["ocr_raw_text"] == "被取消的原始 OCR"
    assert cancelled["vision_mode"] == "parallel"

    # --- 字段缺失时写空串而不是 None（列有 DEFAULT ''，前端按字符串用）---
    monkeypatch.setattr(
        SolutionPipeline,
        "run",
        _fake_run({"status": "completed", "path": answer, "timings": {}}),
    )
    manager.create_task("legacy-core", 1)
    service.run("legacy-core", [], lambda event: None)
    legacy = manager.get_task("legacy-core")
    assert legacy["problem_text"] == ""
    assert legacy["ocr_raw_text"] == ""
    assert legacy["vision_mode"] == ""


def test_failed_run_still_persists_cached_transcript(tmp_path, monkeypatch):
    """F5：求解失败时，视觉阶段（已经付过钱）的转录仍要落库。

    异常路径拿不到 core 的返回值（result 不存在），唯一真实来源是视觉阶段缓存；
    不补写的话 problem_text/ocr_raw_text/vision_mode 会永远为空 —— 用户重试时
    看不到转录，历史搜索也搜不到这次已经付过钱的识别结果。
    """
    from problem_solver_agent import config as core_config
    from problem_solver_agent.core_pipeline import SolutionPipeline
    from webapp.pipeline import PipelineService

    manager = TaskManager(tmp_path / "tasks.db")
    service = PipelineService(tmp_path / "solutions", manager)
    manager.create_task("boom", 2)
    manager.set_cached_stage(
        "boom",
        "vision",
        {
            "_meta": {
                "model": core_config.VISION_CLASSIFY_MODEL,
                "provider": core_config.VISION_PROVIDER_NAME,
                "max_tokens": 4096,
                "protocol": getattr(SolutionPipeline, "_CACHE_PROTOCOL", "page-v1"),
            },
            "value": {
                "problem_type": "GENERAL",
                "pages": ["第一页题干", "第二页题干"],
                "failed_pages": [],
                "continuations": [False, True],
                "vision_mode": "combined",
            },
        },
    )

    def _boom(self, task_id, image_paths, **kwargs):
        raise RuntimeError("求解器崩了")

    monkeypatch.setattr(SolutionPipeline, "run", _boom)
    with pytest.raises(RuntimeError):
        service.run("boom", [], lambda event: None)

    task = manager.get_task("boom")
    assert task["status"] == "failed"
    assert "求解器崩了" in task["error_message"]
    # CONT 页用单个换行拼接（join_by_continuation 的约定）
    assert task["problem_text"] == "第一页题干\n第二页题干"
    assert task["ocr_raw_text"] == "第一页题干\n第二页题干"
    assert task["vision_mode"] == "combined"


def test_failed_run_with_unusable_cache_leaves_transcript_empty(tmp_path, monkeypatch):
    """缓存不可用（这里是迁移前的裸 payload）时，宁可不写也不写编造的文本。"""
    from problem_solver_agent.core_pipeline import SolutionPipeline
    from webapp.pipeline import PipelineService

    manager = TaskManager(tmp_path / "tasks.db")
    service = PipelineService(tmp_path / "solutions", manager)
    manager.create_task("boom2", 1)
    manager.set_cached_stage(
        "boom2", "vision", {"problem_type": "GENERAL", "pages": ["旧格式的题目文本"]}
    )

    def _boom(self, task_id, image_paths, **kwargs):
        raise RuntimeError("又崩了")

    monkeypatch.setattr(SolutionPipeline, "run", _boom)
    with pytest.raises(RuntimeError):
        service.run("boom2", [], lambda event: None)

    task = manager.get_task("boom2")
    assert task["status"] == "failed"
    assert task["problem_text"] == ""
    assert task["ocr_raw_text"] == ""
    assert task["vision_mode"] == ""


def test_recent_tasks_keep_keys_but_drop_heavy_text(tmp_path):
    """F8：列表行不含重文本内容，但三个转录键必须保留（前端 TS 类型依赖）。"""
    manager = TaskManager(tmp_path / "tasks.db")
    manager.create_task("t1", 1)
    manager.update_task(
        "t1", problem_text="润色后的题目", ocr_raw_text="原始识别文本", vision_mode="json"
    )

    row = manager.get_recent_tasks()[0]
    assert row["problem_text"] == ""
    assert row["ocr_raw_text"] == ""
    assert row["vision_mode"] == "json"

    # 单任务 getter 仍然是完整文本（历史详情页要用）
    full = manager.get_task("t1")
    assert full["problem_text"] == "润色后的题目"
    assert full["ocr_raw_text"] == "原始识别文本"


def test_list_and_search_omit_heavy_transcript_text(tmp_path, monkeypatch):
    """F8：`GET /api/tasks` 与 `?q=` 的响应不再携带可能数 MB 的转录文本。"""
    from fastapi.testclient import TestClient

    from webapp import config as web_config
    from webapp.app import create_app

    monkeypatch.setattr(web_config, "UPLOAD_DIR", tmp_path / "uploads", raising=False)
    monkeypatch.setattr(web_config, "SOLUTION_DIR", tmp_path / "solutions", raising=False)
    monkeypatch.setattr(web_config, "DATA_DIR", tmp_path / "data", raising=False)
    monkeypatch.setattr(web_config, "DB_PATH", tmp_path / "data" / "tasks.db", raising=False)

    heavy = "OCR" * 2000  # 模拟多页原始文本
    app = create_app()
    with TestClient(app) as client:
        from webapp import routes

        routes.task_manager.create_task("heavy", 1)
        routes.task_manager.update_task(
            "heavy", problem_text=heavy, ocr_raw_text=heavy, vision_mode="combined"
        )

        listed = client.get("/api/tasks").json()["tasks"]
        row = next(t for t in listed if t["id"] == "heavy")
        assert row["problem_text"] == ""
        assert row["ocr_raw_text"] == ""
        assert row["vision_mode"] == "combined"

        # 搜索仍然命中这两列（过滤在 SQL 里做），只是不下发内容
        searched = client.get("/api/tasks", params={"q": "OCR"}).json()["tasks"]
        hit = next(t for t in searched if t["id"] == "heavy")
        assert hit["problem_text"] == ""
        assert hit["ocr_raw_text"] == ""

        # 单任务详情必须仍然是完整文本
        detail = client.get("/api/tasks/heavy").json()["task"]
        assert detail["problem_text"] == heavy
        assert detail["ocr_raw_text"] == heavy


def test_list_tasks_endpoint_supports_q(tmp_path, monkeypatch):
    """`GET /api/tasks?q=` 必须走搜索，且保留原有的可见性过滤。"""
    from fastapi.testclient import TestClient

    from webapp import config as web_config
    from webapp.app import create_app

    monkeypatch.setattr(web_config, "UPLOAD_DIR", tmp_path / "uploads", raising=False)
    monkeypatch.setattr(web_config, "SOLUTION_DIR", tmp_path / "solutions", raising=False)
    monkeypatch.setattr(web_config, "DATA_DIR", tmp_path / "data", raising=False)
    monkeypatch.setattr(web_config, "DB_PATH", tmp_path / "data" / "tasks.db", raising=False)

    app = create_app()
    with TestClient(app) as client:
        from webapp import routes

        routes.task_manager.create_task("hit", 1)
        routes.task_manager.create_task("miss", 1)
        routes.task_manager.update_task("hit", ocr_raw_text="含有关键词 洛必达 的原始识别")
        routes.task_manager.update_task("miss", ocr_raw_text="无关内容")

        # 不带 q：维持原列表语义
        all_ids = {t["id"] for t in client.get("/api/tasks").json()["tasks"]}
        assert {"hit", "miss"} <= all_ids

        # 带 q：只返回命中项
        hit_ids = {t["id"] for t in client.get("/api/tasks", params={"q": "洛必达"}).json()["tasks"]}
        assert hit_ids == {"hit"}

        # 空白 q 不应退化成"列出全部"之外的行为（视为未搜索，仍走列表）
        blank = client.get("/api/tasks", params={"q": "   "}).json()["tasks"]
        assert {"hit", "miss"} <= {t["id"] for t in blank}
