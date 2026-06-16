# 前端（React + TypeScript + Vite）

这是「自动化多图解题 Agent」的 Web 界面，构建产物输出到 `../webapp/static/`，
由 FastAPI 的 `StaticFiles` 挂载提供服务。

技术栈：React 19 · TypeScript · Vite · Tailwind CSS 4 · Zustand · react-markdown + KaTeX

---

## 常用命令

```powershell
npm install      # 安装依赖
npm run dev      # 开发服务器 http://localhost:5173（/api 代理到 8000）
npm run build    # 类型检查 + 生产构建 → ../webapp/static/
npm run lint     # ESLint
```

> **改完源码必须 `npm run build`**，否则 8000 端口上跑的仍是旧产物（浏览器还需刷新）。

开发时建议同时启动后端：

```powershell
# 终端 1（项目根目录）
python run_web.py
# 终端 2
cd frontend && npm run dev
```

`vite.config.ts` 已把 `/api`、`/solutions`、`/uploads` 代理到 `localhost:8000`。

---

## 目录结构

```
src/
├── api/client.ts                 # 兼容层，转出 lib/api
├── lib/
│   ├── api.ts                    # 全部 HTTP 调用 + ApiError
│   ├── utils.ts                  # cn / formatTs / formatDuration
│   ├── taskStatus.ts             # 状态标签与终态判断
│   └── problems.ts               # 题型 → 中文标签
├── features/stream/taskStream.ts # 唯一的 SSE 连接实现（退避重连等）
├── hooks/useMediaQuery.ts        # 移动端断点
├── stores/
│   ├── useTaskStore.ts           # 流式进度、连接注册表、全局 SSE
│   ├── useUploadStore.ts         # 待上传文件队列
│   └── useLayoutStore.ts         # 分栏比例、灯箱图片集合
└── components/
    ├── ErrorBoundary.tsx         # 渲染期错误兜底
    ├── layout/                   # AppHeader / SplitPanelLayout / MobileLayout / QrCodeButton
    ├── upload/                   # UploadZone / FilePreviewList / UploadActions
    ├── viewer/                   # ImageViewer / ImageLightbox
    ├── output/                   # OutputPanel / lazy / MarkdownRenderer / ProgressSteps
    │                             # ThinkingBlock / TimingBreakdown / ReadingMode
    ├── tasks/                    # TaskHistoryPage / TaskCard
    ├── settings/                 # SettingsPage
    └── ui/                       # Button / Card / Badge / Dialog
```

路由（HashRouter，便于直接由静态文件托管）：

| 路径 | 页面 |
|---|---|
| `/` | 新建任务（上传 / 粘贴 / 拖拽） |
| `/task/:taskId` | 任务详情与实时解答 |
| `/history` | 任务列表 |
| `/settings` | 运行状态、磁盘占用、阶段耗时 |

---

## 性能注意事项

首屏 JS 曾是一个 785 KB 的单包。**markdown + KaTeX 约占 420 KB**，只在真正要看
解答时才需要，因此放进 `components/output/lazy.tsx` 做动态分包：

| 分包 | 体积 | 何时加载 |
|---|---|---|
| `index-*.js` | 约 341 KB | 首屏 |
| `MarkdownRenderer-*.js` | 约 420 KB | 首次渲染解答/思考过程时 |

**因此：不要在首屏路径上静态导入 `MarkdownRenderer` / `ReadingMode`**，
请通过 `lazy.tsx` 里的包装组件使用，否则分包会失效、首屏体积回退。

---

## 状态管理约定

- **服务端数据**（任务列表、任务详情）走 `lib/api.ts`，由页面组件自己持有
- **流式进度**放在 `useTaskStore`，键为 `taskId`；思考过程与正文缓冲有 200 KB
  上限，超出后截断并追加提示，避免长任务把内存吃满
- **SSE 连接**只能通过 `features/stream/taskStream.ts` 创建。不要在组件里直接
  `new EventSource(...)`——重连、退避、可见性恢复这些逻辑都在那里

---

## 与后端协作

接口契约见 `docs/API.md`，类型定义见 `src/types/index.ts`。改接口时两侧同步更新。

SSE 事件用 `event: <type>` 命名，因此必须逐类型注册监听（`addEventListener`），
不能只依赖 `onmessage`。
