# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

自动化多图解题Agent — 通过实时文件监控、智能分组与多模型协同，将连续截图自动转化为结构化 AI 解答。提供两种入口：**命令行 Agent**（监控截图目录）和 **Web 应用**（FastAPI + 浏览器上传图片 + SSE 流式输出）。

## 常用命令

### 安装依赖
```bash
pip install -r requirements.txt
```

### 启动命令行 Agent
```bash
python -m problem_solver_agent.main
```

### 启动 Web 应用
```bash
python run_web.py
```

### 运行独立工具（均需管理员权限）
```bash
python tools/silent_screencapper.py   # 热键截图（默认 Alt+X）
python tools/remote_trigger.py        # 手机远程遥控截图（端口 5555）
python tools/human_typer.py           # 模拟真人打字输出 AI 代码
```

**注意**：项目没有测试文件，没有 lint/format 配置。运行前需要在项目根目录创建 `.env` 文件，配置 `DEEPSEEK_API_KEY` 和 `ZHIPU_API_KEY`。

## 架构

### 双入口设计

项目有两个独立的处理流水线，共享核心的 `problem_solver_agent` 包：

1. **命令行 Agent** (`problem_solver_agent/`) — 事件驱动架构，基于 `watchdog` 监控目录 + 生产者-消费者线程池
2. **Web 应用** (`webapp/`) — FastAPI + Jinja2 模板 + SSE 流式推送，提供 Web UI 上传图片并实时查看解答

两者的流水线步骤完全相同（分类 → OCR → 润色 → 求解 → 命名），但各自在 `image_grouper.py` 和 `webapp/pipeline.py` 中独立实现，未共享流水线代码。

### 核心包 `problem_solver_agent/`

| 模块 | 职责 |
|---|---|
| `main.py` | 入口：健康检查 → 启动 ImageGrouper → 启动 FileMonitor → 阻塞等待 |
| `config.py` | 所有配置中心：API 密钥、模型名称、路径、超时、热键等。切换模型只需改这里的常量 |
| `file_monitor.py` | 基于 `watchdog` 的文件系统监控，检测新截图 → 传递给 ImageGrouper |
| `image_grouper.py` | **核心调度器**：时间窗口分组（`threading.Timer`，默认 8 秒）+ 线程池并发处理 + 完整流水线编排 |
| `vision_client.py` | 视觉 API 客户端（provider-agnostic）：封装分类、OCR 转录、视觉推理。底层使用 OpenAI SDK 兼容 Zhipu GLM-4.6V |
| `solver_client.py` | 求解器客户端：流式/非流式调用 LLM，支持多 provider，内置自动重试 |
| `prompts.py` | 所有 Prompt 模板（Vision/Auxiliary/Solver 三类），使用类作为命名空间组织 |
| `utils.py` | 日志单例、Base64 编码、文件名清理、题号提取 |

### Web 应用 `webapp/`

| 模块 | 职责 |
|---|---|
| `app.py` | FastAPI 应用工厂：创建目录、初始化 Jinja2 模板（含自定义过滤器）、挂载静态文件、预热 API 客户端 |
| `config.py` | Web 专用配置：上传/解答/数据目录、端口、文件大小限制 |
| `models.py` | `TaskManager`：基于 sqlite3 的任务持久化 CRUD（WAL 模式） |
| `routes.py` | 页面路由 + REST API + SSE 流式端点。`TaskEventBus` 实现 per-task 事件广播 |
| `pipeline.py` | `PipelineService`：Web 端的处理流水线，通过 `on_progress` 回调推送 SSE 事件 |

### 流水线步骤

```
截图 → 视觉分类(问题类型) → OCR转录(并行多图) → 文本合并润色 → 智能重分类(ML/编程分流) → 求解器流式生成 → LLM生成文件名 → 归档
```

- **重分类逻辑**：OCR 后检测关键词（numpy/torch/代码等），将初步分类修正为 ML_CODING / CODING
- **编程题风格**：`SOLUTION_STYLE` 控制 `OPTIMAL`（最优解）或 `EXPLORATORY`（面试讲解式）

### Provider 系统

求解器通过 `SOLVER_CONFIG` 字典管理多个 provider（如 deepseek、zhipu），每个 provider 有独立的 `base_url` 和 `model`。客户端是 provider-agnostic 的 — 添加新 provider 只需在 `config.py` 中添加一个条目并在 `.env` 中配置 `{PROVIDER}_API_KEY`（全大写）。

## 关键约定

- **Starlette `TemplateResponse` 参数顺序**：`(request, name, context)`，不是 `(name, context)` — 遗漏 `request` 会导致 500 错误
- **API 密钥命名**：在 `SOLVER_CONFIG` 中添加 provider `"xyz"` 后，必须在 `.env` 中配置 `XYZ_API_KEY`
- **路径**：核心 Agent 的 `ROOT_DIR` 默认为项目目录的**父目录**，可通过 `SOLVER_ROOT_DIR` 环境变量覆盖。Web 应用的路径都在 `webapp/` 子目录下
- **_fix_routes.py** 是根目录下的一个独立副本（用于修复路由问题），不是正式模块
