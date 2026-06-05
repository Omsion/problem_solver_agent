# 重构方案：Web 端 React 重构

## Context

将当前项目的 Web 前端从 **FastAPI + Jinja2 模板 + 原生 JS** 重构为 **FastAPI + React + TypeScript**，参考另一个项目（Next.js PDF 阅读器）的架构模式，实现：
- **左侧**：图片上传/预览/查看器
- **右侧**：AI 大模型流式输出面板（含思考过程展示）
- **现代化 UI**：Tailwind CSS + Zustand + resizable panels

## 技术选型

| 层 | 技术 | 说明 |
|---|---|---|
| 前端框架 | React 19 + TypeScript 5 | Vite 6 构建 |
| 样式 | Tailwind CSS 4 | 通过 @tailwindcss/vite 插件 |
| 状态管理 | Zustand 5 | 细粒度订阅，避免不必要的 re-render |
| 面板布局 | react-resizable-panels | 左右可拖拽调整分栏 |
| Markdown | react-markdown + remark-gfm + remark-math + rehype-katex | 客户端流式渲染 |
| 后端 | FastAPI (不变) | 增强 CORS + 流水线事件 |
| SSE | 原生 EventSource | 每任务一个连接 |

## 关键文件修改

### 后端改动

#### 1. `problem_solver_agent/solver_client.py` — stream_solve 返回类型重构

**问题**：当前 `stream_solve()` 只 yield 文本字符串，丢弃了 DeepSeek 思考模式下的 `reasoning_content`。

**方案**：改为 yield `dict` 类型事件：
```python
# 新增 reasoning_content 捕获
for chunk in completion:
    delta = chunk.choices[0].delta
    if getattr(delta, 'reasoning_content', None):
        yield {"type": "reasoning", "content": delta.reasoning_content}
    if delta.content:
        yield {"type": "content", "content": delta.content}
```
同时添加 `stream_solve_text_only()` 向后兼容包装器供 CLI Agent (`image_grouper.py`) 使用。

#### 2. `webapp/pipeline.py` — 启用思考模式 + 推理事件

- `run()` 新增 `enable_thinking: bool = True` 参数（默认开启）
- 处理 `stream_solve` 返回的 dict 事件，将 `reasoning` 类型独立推送到 SSE
- 将 `enable_thinking` 传递给 `_start_solve()` → `solver_client.stream_solve()`

#### 3. `webapp/routes.py` — SSE 增强

- SSE 端点支持 `?thinking=1` 查询参数
- `asyncio.Queue` 设置 `maxsize=256` 防止内存溢出
- 新增 `init` 事件（连接建立时发送任务元数据）
- 推理事件 (`type: "reasoning"`) 透传到 SSE

#### 4. `webapp/app.py` — CORS + SPA 回退

- 添加 `CORSMiddleware`（允许 `localhost:5173`）
- 生产模式添加 catch-all 路由，服务 `static/index.html`

#### 5. `problem_solver_agent/image_grouper.py` — 适配新接口

- 调用 `stream_solve_text_only()` 替代 `stream_solve()`（CLI 不需要思考内容显示）

### 前端新建 (`frontend/`)

```
frontend/
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts              # 代理 /api → localhost:8000
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── index.css               # Tailwind v4 + KaTeX
│   ├── types/index.ts          # SSEEvent, Task, UploadFile
│   ├── api/client.ts           # fetch 封装
│   ├── stores/
│   │   ├── useUploadStore.ts   # 上传文件队列
│   │   ├── useTaskStore.ts     # 任务注册表 + SSE 连接管理
│   │   └── useLayoutStore.ts   # 面板尺寸、灯箱状态
│   ├── hooks/
│   │   └── useSSE.ts           # SSE 连接 hook
│   ├── components/
│   │   ├── layout/
│   │   │   ├── AppHeader.tsx       # 顶部导航栏
│   │   │   └── SplitPanelLayout.tsx # 左右可调整分栏
│   │   ├── upload/
│   │   │   ├── UploadZone.tsx      # 拖拽/点击/粘贴上传
│   │   │   ├── FilePreviewList.tsx  # 缩略图预览网格
│   │   │   └── UploadActions.tsx   # 清除 + 开始解答按钮
│   │   ├── viewer/
│   │   │   ├── ImageViewer.tsx     # 左面板：图片查看（缩放/平移）
│   │   │   ├── ImageToolbar.tsx    # 缩放/旋转/全屏控制
│   │   │   └── ImageLightbox.tsx  # 全屏灯箱
│   │   ├── output/
│   │   │   ├── OutputPanel.tsx     # 右面板：解答/思考过程标签页
│   │   │   ├── MarkdownRenderer.tsx # react-markdown + KaTeX
│   │   │   ├── ThinkingBlock.tsx   # 可折叠推理过程
│   │   │   └── ProgressSteps.tsx   # 流水线阶段指示器
│   │   ├── tasks/
│   │   │   ├── TaskHistoryPage.tsx # 历史任务表格
│   │   │   └── TaskCard.tsx       # 任务卡片
│   │   └── ui/
│   │       ├── button.tsx
│   │       ├── card.tsx
│   │       ├── badge.tsx
│   │       └── dialog.tsx
│   └── lib/
│       └── utils.ts              # cn() = clsx + tailwind-merge
```

### 数据流

```
[用户上传图片]
  → UploadZone (drag/drop/click/paste)
  → useUploadStore.addFiles()
  → 用户点击 "开始解答"
  → useTaskStore.createTask(files) → POST /api/tasks → { task_id }
  → useTaskStore.connectSSE(taskId) → new EventSource(/api/tasks/{id}/stream?thinking=1)
  → SSE 事件流:
      init       → 设置任务元数据
      status     → ProgressSteps 更新阶段
      reasoning  → ThinkingBlock 追加推理内容
      chunk      → MarkdownRenderer 追加解答内容
      done       → 标记完成，关闭 EventSource
      error      → 显示错误信息
  → MarkdownRenderer 通过 react-markdown 实时渲染流式内容
```

### 关键设计决策

1. **Zustand 而非 Context**：SSE 事件频繁触发，Zustand 细粒度订阅确保只有相关组件 re-render
2. **客户端 Markdown 渲染**：替代服务端 Jinja2 渲染，支持流式更新无需刷新页面
3. **每任务独立 EventSource**：支持多任务并发，各任务独立流式输出（浏览器限制 ~6 并发连接，实际够用）
4. **Vite 代理开发模式**：前端 `/api/*` 代理到 FastAPI `localhost:8000`，无需跨域配置
5. **向后兼容**：CLI Agent 通过 `stream_solve_text_only()` 包装器保持不变

### SSE 事件类型（完整）

| type | phase/content | 说明 |
|---|---|---|
| `init` | `{task_id, num_images}` | 连接建立，任务元数据 |
| `status` | `{phase: "classifying"/"ocr"/"solving", message}` | 流水线阶段切换 |
| `reasoning` | `{content: "..."}` | 推理/思考过程片段（新增） |
| `chunk` | `{content: "..."}` | 解答文本片段 |
| `done` | `{task_id, filename}` | 流水线完成 |
| `error` | `{message}` | 错误终止 |

## 实施步骤

### Phase 1：后端增强
1. 重构 `solver_client.stream_solve()` → 返回 dict 事件
2. 添加 `stream_solve_text_only()` 兼容包装器
3. 更新 `image_grouper.py` 使用 `stream_solve_text_only()`
4. 增强 `pipeline.py`：`enable_thinking` + reasoning 事件
5. 增强 `routes.py`：SSE `?thinking` 参数 + `init` 事件 + Queue maxsize
6. `app.py`：CORS 中间件

### Phase 2：前端脚手架
1. `npm create vite@latest frontend -- --template react-ts`
2. 安装所有依赖
3. 配置 `vite.config.ts`（代理 + 构建输出路径 `../webapp/static`）
4. 配置 Tailwind CSS v4 + KaTeX
5. 定义 TypeScript 类型
6. 创建 API 客户端
7. 实现 Zustand stores

### Phase 3：核心布局和面板
1. `AppHeader` — 顶部导航
2. `SplitPanelLayout` — react-resizable-panels 分栏
3. `ImageViewer` + `ImageToolbar` — 左侧图片查看器
4. `OutputPanel` — 右侧标签页容器

### Phase 4：上传和图片管理
1. `UploadZone` — 拖拽/粘贴/点击上传
2. `FilePreviewList` — 缩略图网格
3. `UploadActions` — 清除/开始解答
4. `ImageLightbox` — 全屏查看

### Phase 5：流式输出
1. `useSSE` hook — EventSource 生命周期管理
2. `MarkdownRenderer` — react-markdown + KaTeX
3. `ThinkingBlock` — 可折叠推理展示
4. `ProgressSteps` — 流水线阶段动画
5. `TaskCard` — 任务卡片（可折叠）

### Phase 6：历史记录和收尾
1. `TaskHistoryPage` — 历史任务表格
2. React Router 路由（`/` 和 `/history`）
3. `app.py` SPA 回退路由
4. 生产构建测试

## 验证方法

1. **后端**：`python run_web.py` 启动后用 curl 测试 SSE 端点：
   ```bash
   curl -N http://localhost:8000/api/tasks/{id}/stream?thinking=1
   ```
   确认能看到 `init`、`status`、`reasoning`、`chunk`、`done` 事件

2. **开发模式**：
   ```bash
   # Terminal 1
   python run_web.py
   # Terminal 2
   cd frontend && npm run dev
   ```
   访问 `http://localhost:5173`，测试上传图片 → 流式解答完整流程

3. **生产模式**：
   ```bash
   cd frontend && npm run build
   python run_web.py
   ```
   访问 `http://localhost:8000`，确认 React 应用正确加载，功能完整

4. **CLI Agent 兼容**：`python -m problem_solver_agent.main` 确认命令行模式正常工作
