# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

自动化多图解题Agent — 通过实时文件监控、智能分组与多模型协同，将连续截图自动转化为结构化 AI 解答。提供两种入口：**命令行 Agent**（监控截图目录）和 **Web 应用**（FastAPI + React SPA + SSE 流式输出）。

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

**一键启动（推荐）**：双击 `start_web.bat`，自动安装依赖 → 构建前端 → 启动服务。

**手动启动**：
```bash
# 首次运行需先构建前端
cd frontend && npm install && npm run build && cd ..
# 启动后端
python run_web.py
```

### 前端开发与构建
```bash
cd frontend
npm install            # 安装前端依赖
npm run dev            # 启动 Vite 开发服务器 (localhost:5173)
npm run build          # 生产构建 → webapp/static/
npm run lint           # ESLint 检查
```

### 运行独立工具（均需管理员权限）
```bash
python tools/silent_screencapper.py   # 热键截图（默认 Alt+X）
python tools/remote_trigger.py        # 手机远程遥控截图（端口 5555）
python tools/human_typer.py           # 模拟真人打字输出 AI 代码
```

**注意**：项目没有测试文件，没有 Python lint/format 配置。运行前需要在项目根目录创建 `.env` 文件，配置 `DEEPSEEK_API_KEY` 和 `ZHIPU_API_KEY`。

## 架构

### 三部分结构

| 部分 | 路径 | 技术栈 | 职责 |
|---|---|---|---|
| **核心包** | `problem_solver_agent/` | Python + OpenAI SDK | AI 流水线引擎（分类/OCR/求解） |
| **Web 后端** | `webapp/` | FastAPI + SQLite + SSE | REST API + 流式推送 + 任务持久化 |
| **Web 前端** | `frontend/` | React 19 + TypeScript + Vite + Tailwind CSS 4 + Zustand | SPA 用户界面 |

### 双入口设计

项目有两个独立的处理流水线，共享核心的 `problem_solver_agent` 包：

1. **命令行 Agent** (`problem_solver_agent/`) — 事件驱动架构，基于 `watchdog` 监控目录 + 生产者-消费者线程池
2. **Web 应用** (`webapp/` + `frontend/`) — FastAPI 后端 + React SPA 前端，浏览器上传图片 + SSE 流式推送

两者的流水线步骤完全相同（分类 → OCR → 润色 → 求解 → 命名），但各自在 `image_grouper.py` 和 `webapp/pipeline.py` 中独立实现，未共享流水线代码。

### 核心包 `problem_solver_agent/`

| 模块 | 职责 |
|---|---|
| `main.py` | 入口：健康检查 → 启动 ImageGrouper → 启动 FileMonitor → 阻塞等待 |
| `config.py` | **纯常量**：API 密钥、模型名称、路径、超时、关键词。不含任何函数或业务逻辑 |
| `pipeline.py` | **共享流水线逻辑**：重分类、类型映射、求解器路由、初始化/验证 |
| `prompts.py` | **Prompt 模板**（模块级常量）：分类/转录/润色/求解/命名等所有 Prompt |
| `file_monitor.py` | 基于 `watchdog` 的文件系统监控，检测新截图 → 传递给 ImageGrouper |
| `image_grouper.py` | **核心调度器**：时间窗口分组（`threading.Timer`，默认 8 秒）+ 线程池并发处理 + 完整流水线编排 |
| `vision_client.py` | 视觉 API 客户端（provider-agnostic，直接导入 prompts 模块） |
| `solver_client.py` | 求解器客户端：流式/非流式调用 LLM，支持多 provider，内置自动重试 |
| `utils.py` | 日志单例、Base64 编码、文件名清理、题号提取 |

### Web 后端 `webapp/`

| 模块 | 职责 |
|---|---|
| `app.py` | FastAPI 应用工厂：创建目录、挂载静态文件、CORS、SPA fallback、预热 API 客户端 |
| `config.py` | Web 专用配置：上传/解答/数据目录、端口、文件大小限制 |
| `models.py` | `TaskManager`：基于 sqlite3 的任务持久化 CRUD（WAL 模式） |
| `routes.py` | REST API + SSE 流式端点。`TaskEventBus` 实现 per-task 事件广播。页面路由已移除，由 SPA fallback 接管 |
| `pipeline.py` | `PipelineService`：Web 端的处理流水线，通过 `on_progress` 回调推送 SSE 事件 |

### Web 前端 `frontend/`

| 目录/文件 | 职责 |
|---|---|
| `src/App.tsx` | 主应用组件（HashRouter），路由：`/` 主页、`/tasks` 历史 |
| `src/main.tsx` | ReactDOM 入口 |
| `src/index.css` | Tailwind CSS v4 + KaTeX 样式 |
| `src/types/index.ts` | TypeScript 类型定义（SSEEvent、Task、UploadFile 等） |
| `src/api/client.ts` | API 封装：`createTask`、`getTask`、`listTasks`、`deleteTask`、`sseUrl` |
| `src/hooks/useSSE.ts` | EventSource 生命周期管理 hook |
| `src/stores/useTaskStore.ts` | Zustand store：任务注册表 + SSE 连接管理 |
| `src/stores/useUploadStore.ts` | Zustand store：上传文件队列 |
| `src/stores/useLayoutStore.ts` | Zustand store：面板尺寸、灯箱状态 |
| `src/lib/utils.ts` | `cn()` = clsx + tailwind-merge 工具函数 |
| `src/components/layout/` | AppHeader、SplitPanelLayout |
| `src/components/upload/` | UploadZone、FilePreviewList、UploadActions |
| `src/components/viewer/` | ImageViewer、ImageToolbar、ImageLightbox |
| `src/components/output/` | OutputPanel、MarkdownRenderer、ThinkingBlock、ProgressSteps |
| `src/components/tasks/` | TaskHistoryPage、TaskCard |
| `src/components/ui/` | Button、Card、Badge、Dialog（原子组件） |

构建输出到 `webapp/static/`，由 FastAPI 的 `StaticFiles` 挂载 + SPA fallback 路由提供服务。

### SSE 事件协议

后端通过 SSE 推送以下事件类型（JSON 格式，`data:` 行）：

| type | 携带字段 | 说明 |
|---|---|---|
| `init` | `task_id`, `num_images` | 连接建立后立即发送 |
| `status` | `phase`, `message` | 流水线阶段切换（classifying / ocr / solving） |
| `chunk` | `content` | 求解器流式输出片段 |
| `reasoning` | `content` | DeepSeek 思考过程（仅 `enable_thinking=True` 时） |
| `done` | `task_id`, `filename` | 流水线成功完成 |
| `error` | `message` | 流水线失败 |

### 流水线步骤

```
截图 → 视觉分类(问题类型) → OCR转录(并行多图) → 文本合并润色 → 智能重分类(ML/编程分流) → 求解器流式生成 → LLM生成文件名 → 归档
```

- **重分类逻辑**：OCR 后检测关键词（numpy/torch/代码等），将初步分类修正为 ML_CODING / CODING
- **编程题风格**：`SOLUTION_STYLE` 控制 `OPTIMAL`（最优解）或 `EXPLORATORY`（面试讲解式）

### Provider 系统

求解器通过 `SOLVER_CONFIG` 字典管理多个 provider（如 deepseek、zhipu），每个 provider 有独立的 `base_url` 和 `model`。客户端是 provider-agnostic 的 — 添加新 provider 只需在 `config.py` 中添加一个条目并在 `.env` 中配置 `{PROVIDER}_API_KEY`（全大写）。

视觉模型固定使用智谱 GLM-4.6V 系列，配置在 `VISION_CLASSIFY_MODEL` / `VISION_REASONING_MODEL`。

## 关键约定

- **Starlette `TemplateResponse` 参数顺序**：`(request, name, context)`，不是 `(name, context)` — 遗漏 `request` 会导致 500 错误
- **API 密钥命名**：在 `SOLVER_CONFIG` 中添加 provider `"xyz"` 后，必须在 `.env` 中配置 `XYZ_API_KEY`
- **路径**：核心 Agent 的 `ROOT_DIR` 默认为项目目录的**父目录**，可通过 `SOLVER_ROOT_DIR` 环境变量覆盖。Web 应用的路径都在 `webapp/` 子目录下
- **前端构建**：修改 `frontend/` 源码后必须 `npm run build` 才能在生产环境生效。开发时可用 `npm run dev` 启动 Vite 开发服务器，API 请求通过 CORS 跨域
- **SSE 事件**：后端推送 `event:` + `data:` 标准格式。`pipeline.py` 中保留的非 dict 回退分支（第 76-79 行）是遗留兼容代码，不应触发

## 已知技术债务
1. 手机局域网扫码连接后，手机视角为什么完全变形了，我需要默认显示浏览器里的“桌面版网站"
2. 手机局域网扫码连接后，右上角的”手机扫码“按钮就消失，不要一直显示了
3. 点击“历史记录”中的不同历史进行切换时，为什么无法直接显示出来，会卡为白色屏幕，还需要点击浏览器的书安心进行主动刷新才会正常显示
4. 查看历史记录时，我不需要“思考过程"的相关内容（只有第一次从主页进行开始解答时才需要）
5. 第一次从主页输入图片，进行开始解答时需要“思考过程"的相关内容，思考过程"的相关内容需要自动滑动底部，除非我使用右侧的滑动条进行内容的上下浏览
6. 为什么会出现类似于“INFO:     127.0.0.1:9130 - "GET /static/assets/index-eHnL2Tiv.js HTTP/1.1" 304 Not Modified
INFO:     127.0.0.1:9130 - "GET /static/assets/index-0iaM7m-C.css HTTP/1.1" 304 Not Modified
INFO:     127.0.0.1:9130 - "GET /api/tasks/92baf6e80197 HTTP/1.1" 200 OK
INFO:     127.0.0.1:9130 - "GET /static/assets/KaTeX_Math-BoldItalic-CZnvNsCZ.woff2 HTTP/1.1" 304 Not Modified
INFO:     127.0.0.1:4570 - "GET /static/assets/KaTeX_Main-Regular-B22Nviop.woff2 HTTP/1.1" 304 Not Modified
INFO:     127.0.0.1:9130 - "GET /static/assets/KaTeX_Math-Italic-t53AETM-.woff2 HTTP/1.1" 304 Not Modified”里的“304 Not Modified”？如何修复？
7. 