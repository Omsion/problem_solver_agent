# 自动化多图解题 Agent - 分阶段改进计划

## 项目概述

这是一个"自动化多图解题Agent"项目，包含：
- **CLI Agent**: 基于 watchdog 监控截图目录，自动处理
- **Web 应用**: FastAPI + React SPA，浏览器上传 + SSE 流式输出
- **核心包**: `problem_solver_agent/`（AI 流水线引擎）

---

## 发现的问题清单（按优先级排序）

### P0 紧急（必须立即修复）

| 序号 | 问题 | 影响文件 | 严重程度 |
|------|------|---------|---------|
| 1 | `vision_client.py` 第 165 行和 194 行重复定义 `transcribe_images_raw` 函数，第一个定义是死代码且 docstring 错误 | `problem_solver_agent/vision_client.py` | 致命 Bug |
| 2 | `config.py` 第 126-155 行重复定义了 `reclassify_problem_type`、`map_final_type`、`determine_solver` 三个函数（与 pipeline.py 完全相同） | `problem_solver_agent/config.py` | 维护陷阱 |
| 3 | 已知技术债务：监控 MONITOR_DIR = ROOT_DIR / "Screenshots"，使得截图自动输入到前端界面 | 新增功能 | 功能缺失 |

### P1 高优先级

| 序号 | 问题 | 影响文件 | 严重程度 |
|------|------|---------|---------|
| 4 | CLI 与 Web 流水线 Prompt 填充方式不一致：CLI 用 `.replace()`，Web 用 `.format()`，Web 有转义但 CLI 没有 | `problem_solver_agent/image_grouper.py`, `webapp/pipeline.py` | 潜在 Bug |
| 5 | SSE 连接缺少超时/取消机制（`routes.py` 第 167 行 `await q.get()` 无限阻塞） | `webapp/routes.py` | 连接挂起 |
| 6 | 测试覆盖率极低：仅 `test_config.py` 一个测试文件，核心逻辑零覆盖 | 整个项目 | 高重构风险 |

### P2 中优先级

| 序号 | 问题 | 影响文件 | 严重程度 |
|------|------|---------|---------|
| 7 | `vision_client.py` 第 59 行 `extra_params: dict = None` 类型错误（应为 `dict | None`） | `problem_solver_agent/vision_client.py` | 类型不安全 |
| 8 | 多处 `except OSError: pass` 静默吞掉异常，无日志 | `webapp/pipeline.py`, `webapp/routes.py` | 调试困难 |
| 9 | 没有自定义异常层次，全用 `Exception` | 整个项目 | 错误处理粗糙 |
| 10 | API 密钥使用 `getattr(config, ...)` 反射获取，缺少 provider 名称校验 | `problem_solver_agent/solver_client.py` | 潜在安全风险 |
| 11 | 两套日志系统不一致（CLI vs Web） | `problem_solver_agent/utils.py`, `webapp/*` | 运维困难 |

### P3 低优先级

| 序号 | 问题 | 影响文件 | 严重程度 |
|------|------|---------|---------|
| 12 | `_fix.py` 调试残留文件 | 项目根目录 | 代码整洁性 |
| 13 | SQLite 连接每次创建，可考虑长连接 | `webapp/models.py` | 轻微性能 |
| 14 | 工作线程无优雅退出机制 | `problem_solver_agent/image_grouper.py` | 数据丢失风险 |
| 15 | `stream_task` 函数过长（68行） | `webapp/routes.py` | 可读性差 |

---

## 分阶段改进方案

### 阶段 1: 紧急修复 (P0)

**目标**: 修复致命 Bug 和明显的代码重复问题

#### 1.1 vision_client.py 重复函数清理

**修改文件**: `problem_solver_agent/vision_client.py`

**具体改动**:
```python
# 删除第165-191行的重复定义（第一个 transcribe_images_raw）
# 保留第194-224行的正确实现

# 同时修复第59行的类型提示错误
# 原: extra_params: dict = None
# 改: extra_params: dict | None = None
```

**验证方案**:
```bash
python -c "from problem_solver_agent.vision_client import transcribe_images_raw; print('OK')"
```

#### 1.2 config.py 重复函数清理

**修改文件**: `problem_solver_agent/config.py`

**具体改动**:
```python
# 删除第126-155行的所有重复函数定义
# 确保所有调用方正确导入 pipeline.py 中的函数
```

**检查调用方验证**:
```bash
grep -n "from .config import.*reclassify\|from . import config.*reclassify" problem_solver_agent/*.py
# 确认所有调用都使用 pipeline.py 中的函数
```

**复杂度**: 低（2-4小时）
**风险**: 删除函数可能导致导入错误
**回滚方案**: git checkout 恢复原文件

---

### 阶段 2: 功能增强 - 截图文件夹自动导入 Web 前端

**目标**: 实现 CLI 监控的截图自动推送到 Web 前端并触发解答流水线

#### 2.1 架构设计

**数据流**:
```
MONITOR_DIR (截图文件夹)
    ↓ (watchdog 监控)
WebFileMonitor (新模块)
    ↓ (检测新文件)
ImageGrouper (时间窗口分组)
    ↓ (图片组准备好)
WebPipelineBridge (桥接层)
    ↓ (创建任务 + 复制图片)
TaskManager + PipelineService
    ↓ (SSE事件)
前端 Zustand store + TaskEventBus
    ↓ (实时推送)
React UI (自动显示新任务)
```

#### 2.2 后端实现

**新增文件**: `webapp/auto_import.py`

```python
"""自动导入监控模块 - 桥接 CLI ImageGrouper 与 Web Pipeline

职责:
1. 复用现有 file_monitor.py 的 watchdog 机制
2. 检测到新截图后，通过 ImageGrouper 时间窗口分组
3. 分组完成后，自动创建 Web Task 并触发流水线
4. 通过 SSE 事件总线推送状态到前端
"""

import threading
import time
import uuid
import shutil
from pathlib import Path
from typing import Callable

from problem_solver_agent import config as core_config
from problem_solver_agent.file_monitor import start_monitoring
from problem_solver_agent.image_grouper import ImageGrouper
from problem_solver_agent.utils import setup_logger

from . import config as web_config
from .routes import event_bus

logger = setup_logger()


class WebAutoImporter:
    """自动截图导入器 - 连接 CLI 监控与 Web 流水线"""

    def __init__(self, task_manager, pipeline_service):
        self.task_manager = task_manager
        self.pipeline_service = pipeline_service
        self.image_grouper = ImageGrouper(num_workers=1)
        self._observer = None
        self._running = False
        self._lock = threading.Lock()
        self._processing_tasks = set()  # 防重复

    def start(self):
        """启动监控"""
        if self._running:
            return

        # 覆盖 ImageGrouper 的 _execute_pipeline，改为调用 Web 流水线
        original_execute = self.image_grouper._execute_pipeline
        self.image_grouper._execute_pipeline = self._web_pipeline_wrapper

        self._observer = start_monitoring(core_config.MONITOR_DIR, self.image_grouper)
        self._running = True
        logger.info("Web 自动截图导入已启动，监控目录: %s", core_config.MONITOR_DIR)

    def stop(self):
        """停止监控"""
        if self._observer:
            self._observer.stop()
            self._observer.join()
        self._running = False

    def _web_pipeline_wrapper(self, image_group: list[Path]):
        """包装 ImageGrouper 的执行，调用 Web 流水线"""
        task_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"

        # 防重复检查
        with self._lock:
            group_key = tuple(sorted(p.name for p in image_group))
            if group_key in self._processing_tasks:
                logger.warning("检测到重复任务，跳过: %s", group_key)
                return
            self._processing_tasks.add(group_key)

        try:
            # 1. 复制图片到 Web 上传目录
            task_dir = web_config.UPLOAD_DIR / task_id
            task_dir.mkdir(parents=True, exist_ok=True)

            web_image_paths = []
            for img_path in image_group:
                dest = task_dir / img_path.name
                shutil.copy2(img_path, dest)
                web_image_paths.append(dest)

            # 2. 创建 Web 任务
            self.task_manager.create_task(task_id, len(web_image_paths))

            # 3. 推送 SSE 事件通知前端有新任务
            event_bus.publish(task_id, {
                "type": "auto_imported",
                "task_id": task_id,
                "num_images": len(web_image_paths),
                "source": "monitor"
            })

            # 4. 执行 Web 流水线
            def on_progress(event: dict):
                event_bus.publish(task_id, event)

            self.pipeline_service.run(
                task_id,
                web_image_paths,
                on_progress,
                enable_thinking=True
            )

        except Exception as e:
            logger.error("自动导入任务失败 task=%s: %s", task_id, e, exc_info=True)
            event_bus.publish(task_id, {"type": "error", "message": str(e)})
        finally:
            with self._lock:
                self._processing_tasks.discard(group_key)
```

#### 2.3 路由集成

**修改文件**: `webapp/app.py`

在 `create_app()` 中集成自动导入器，在应用启动时在后台线程中启动。

#### 2.4 前端状态管理增强

**修改文件**: `frontend/src/stores/useTaskStore.ts`

- 新增 `autoImportedTasks` 状态列表
- 在 `connectSSE` 中监听 `auto_imported` 事件
- 自动连接新任务的 SSE 并设置为活动任务

#### 2.5 SSE 超时与取消机制完善

**修改文件**: `webapp/routes.py`

```python
# 修复第167行的无限阻塞
# 使用 asyncio.wait_for 增加30秒超时
# 发送心跳保持连接

# 新增任务取消 API
@router.delete("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """取消正在处理的任务"""
```

**复杂度**: 中高（16-24小时）
**风险**: 自动导入可能导致重复处理
**缓解**: 实现去重机制 + 锁文件
**回滚**: 通过配置开关禁用自动导入

---

### 阶段 3: 代码质量提升 (P1-P2)

#### 3.1 CLI 统一 Prompt 填充方式

**目标**: 统一使用 `.format()` 方式，统一转义规则

**修改文件**:
- `problem_solver_agent/image_grouper.py`
- `webapp/pipeline.py`

新增统一的 Prompt 构建工具函数，确保 CLI 和 Web 使用相同的转义规则。

#### 3.2 自定义异常层次

**新增文件**: `problem_solver_agent/exceptions.py`

建立完整的异常层次：
- `SolverError` - 基础异常
- `VisionAPIError` - 视觉 API 异常
- `TranscriptionError` - 转录失败
- `ClassificationError` - 分类失败
- `PipelineError` - 流水线执行异常
- `ConfigError` - 配置错误

#### 3.3 统一日志系统

**修改文件**: `problem_solver_agent/utils.py`

统一日志配置，支持 Web 和 CLI 两种场景，确保格式一致。

#### 3.4 测试策略

**测试覆盖计划**:
1. **单元测试**:
   - `test_vision_client.py` - 视觉客户端测试
   - `test_pipeline.py` - 流水线逻辑测试
   - `test_image_grouper.py` - 分组逻辑测试

2. **集成测试**:
   - `test_web_pipeline.py` - Web 端到端测试
   - `test_sse_stream.py` - SSE 流测试

3. **Mock 策略**:
   - 使用 `unittest.mock` 模拟 OpenAI 客户端
   - 使用 `pytest` 测试框架

**复杂度**: 中（8-12小时）
**风险**: 重构可能引入回归
**缓解**: 先写测试再重构，分步提交

---

### 阶段 4: 长期优化 (P3)

#### 4.1 SQLite 连接池

**修改文件**: `webapp/models.py`

使用 `contextvars` 实现线程本地连接，避免每次操作都创建新连接。

#### 4.2 工作线程优雅退出

**修改文件**: `problem_solver_agent/image_grouper.py`

- 添加 `_stop_event` 线程事件
- 实现 `shutdown()` 方法
- 工作循环使用带超时的 `queue.get()`

#### 4.3 stream_task 函数重构

**修改文件**: `webapp/routes.py`

将 68 行的 `stream_task` 函数拆分为多个子函数：
- `_handle_task_completion()` - 处理已完成任务
- `_handle_processing_start()` - 启动处理
- `_generate_sse_events()` - SSE 事件生成器

**复杂度**: 中（8-16小时）
**风险**: 低，可分步实施

---

## 关键文件修改清单

| 文件路径 | 改动类型 | 阶段 |
|---------|---------|------|
| `problem_solver_agent/vision_client.py` | 删除重复函数 + 修复类型 | 1 |
| `problem_solver_agent/config.py` | 删除重复函数 | 1 |
| `webapp/auto_import.py` | 新增文件 | 2 |
| `webapp/app.py` | 集成自动导入器 | 2 |
| `webapp/routes.py` | SSE 超时/取消机制 | 2 |
| `frontend/src/stores/useTaskStore.ts` | 监听 auto_imported 事件 | 2 |
| `problem_solver_agent/image_grouper.py` | 统一 Prompt 填充 + 优雅退出 | 3, 4 |
| `webapp/pipeline.py` | 统一 Prompt 填充 | 3 |
| `problem_solver_agent/exceptions.py` | 新增文件 | 3 |
| `problem_solver_agent/utils.py` | 统一日志 | 3 |
| `webapp/models.py` | SQLite 连接池 | 4 |
| `tests/test_*.py` | 新增测试文件 | 3 |

---

## 工作量估算

| 阶段 | 预估工时 | 复杂度 |
|------|----------|--------|
| 阶段1: 紧急修复 | 2-4小时 | 低 |
| 阶段2: 功能增强 | 16-24小时 | 中高 |
| 阶段3: 代码质量 | 8-12小时 | 中 |
| 阶段4: 长期优化 | 8-16小时 | 中 |
| **总计** | **34-56小时** | |

---

## 实施建议

1. **优先完成阶段 1**: 修复 P0 Bug 是最紧急的，耗时最短
2. **阶段 2 可分步骤**: 先实现后端桥接，再完善前端集成
3. **测试先行**: 在进行阶段 3 重构前，先补充核心模块的单元测试
4. **阶段 4 可选**: 长期优化项可根据实际需求优先级延后实施
5. **配置开关**: 自动导入功能应可通过环境变量配置开关，便于回滚
