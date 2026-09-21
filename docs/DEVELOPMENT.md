# 开发指南

## 环境

| 组件 | 版本 | 已验证 |
|---|---|---|
| Python | 3.10+ | 3.10.8 |
| Node.js | 20+ | 24.11.1 |
| npm | 10+ | 11.6.2 |

## 常用命令

### 后端

```powershell
# 安装依赖（运行）
pip install -r requirements.txt

# 安装测试依赖
pip install -r requirements-dev.txt

# 运行全部测试（配置见 pytest.ini）
pytest

# 只跑某个文件 / 某个用例
pytest tests/test_core_pipeline.py
pytest tests/test_core_pipeline.py::test_cancellation_during_stream_preserves_partial -v

# 启动 Web 服务（开发时用 --reload 会自动重启）
python run_web.py            # 默认 8000
python run_web.py 9000       # 指定端口

# 启动命令行 Agent
python -m problem_solver_agent.main
```

### 前端

```powershell
cd frontend

npm install          # 首次
npm run dev          # 开发服务器 http://localhost:5173，/api 代理到 8000
npm run build        # 生产构建 → ../webapp/static/（同时做类型检查）
npm run lint         # ESLint
npm test             # vitest 单元测试（54 个用例）
npm run test:watch   # 监听模式
```

> **重要**：`webapp/static/` 里的产物是构建结果。改完 `frontend/src` 必须
> `npm run build`，否则页面仍是旧版本（浏览器还需刷新）。

### 联调

开发时同时开两个终端：

```powershell
# 终端 1
python run_web.py
# 终端 2
cd frontend && npm run dev     # 浏览器访问 5173
```

生产验证用 8000 端口的构建产物（`npm run build` 之后刷新即可）。

---

## 在 PyCharm 中运行

项目内已经准备好了运行配置（`.idea/runConfigurations/`），用 PyCharm 打开项目后
右上角的下拉框里可以直接选：

| 配置名 | 作用 | 地址 |
|---|---|---|
| **一键启动（前后端）** | Compound，同时拉起下面两个 | — |
| 后端服务 (8000) | `run_web.py`，用构建好的 `webapp/static` | http://localhost:8000 |
| 后端服务 (热重载 8000) | `uvicorn --factory --reload`，改 Python 代码自动重启 | http://localhost:8000 |
| 前端服务 (5173) | `npm run dev`，带 HMR 与 `/api` 代理 | http://localhost:5173 |
| 后端测试 (pytest) | 跑 `tests/`（已设 `AUTO_IMPORT_ENABLED=false`） | — |
| 前端测试 (vitest) | `npm test` | — |

### 日常开发怎么选

- **只改前端** → 起「后端服务 (8000)」+「前端服务 (5173)」，浏览器开 **5173**，
  改代码即时热更新。
- **改 Python 代码** → 用「后端服务 (热重载 8000)」，保存后自动重启；
  但注意用这个配置时**不要**同时开「后端服务 (8000)」，两者都占 8000 端口。
- **只验收成品** → 起「后端服务 (8000)」，浏览器开 **8000**
  （用的是 `webapp/static` 里的构建产物，改前端后需要 `npm run build`）。

### 前置条件

1. **Python 解释器**：`File → Settings → Project → Python Interpreter`，
   选择装了 `requirements.txt` 的那个环境（本项目配置里记录的名字是 `llm`）。
   运行配置用的是「模块 SDK」，会自动跟随项目解释器。
2. **Node 插件**：「前端服务」是 npm 类型配置，需要 PyCharm 装了
   *Node.js* 插件（Professional 自带；Community 需在
   `Settings → Plugins → Marketplace` 搜索 *Node.js* 安装）。
3. **Node 解释器**：第一次运行「前端服务」时，PyCharm 会问 Node interpreter，
   选 `C:\Program Files\nodejs\node.exe` 即可。
4. **依赖已安装**：`pip install -r requirements.txt` 与
   `cd frontend && npm install`。

### 常见问题

- **5173 打不开页面**：确认后端也在跑。前端只负责界面，数据来自 8000；
  后端没起时页面能开，但接口会返回 502（代理转发失败）。
- **手机扫码连不上**：局域网需要访问电脑；「前端服务」已开 `host: true`，
  用 `http://<你的局域网IP>:5173` 即可。用 8000 端口则直接是构建产物。
- **改了前端但 8000 端没变化**：8000 提供的是构建产物，需要
  `cd frontend && npm run build`。用 5173 开发则不需要。
- **8000 端口被占用**：换端口时记得同步改 `frontend/vite.config.ts` 里的代理
  `target`（默认写的是 `http://localhost:8000`）。

---

## 代码约定

- 所有 Python 函数**必须有类型提示**（项目约定）
- 代码注释用中文说明**为什么**（背景、坑点），不要复述"做了什么"
- 路径一律用 `pathlib.Path`，不要字符串拼接
- Prompt 模板必须是 **raw 字符串**（`r"""..."""`），否则 LaTeX 里的 `\begin`
  等会被当成非法转义序列
- **不要手写 `new EventSource(...)`**：所有 SSE 连接都走
  `src/features/stream/taskStream.ts`，重连/退避/可见性恢复都在那里
- **不要在首屏路径静态导入 `MarkdownRenderer` / `ReadingMode`**：
  用 `components/output/lazy.tsx` 里的包装组件，否则首屏体积会回退
- **不要用原生 `alert()` / `confirm()`**：用 `ui/toast.tsx` 的 `notify`
  与 `ui/confirm.tsx` 的 `useConfirm()`
- 新增接口后同步更新 `docs/API.md` 与 `frontend/src/types/index.ts`

---

## 测试

### 后端（`tests/`，全部打桩，不产生真实 API 调用）

```powershell
pytest                      # 406 个用例，约 60 秒
pytest tests/test_core_pipeline.py -v
```

| 文件 | 覆盖内容 |
|---|---|
| `test_netcheck.py` | 本机地址判定、移动端 UA 识别（远程连接误报回归） |
| `test_remote_stream.py` | 全局 SSE 端点、`RemotePresence` 计数与断开通知 |
| `test_image_prep.py` | 缩放边界、RGBA 白底、EXIF 方向、缓存命中、损坏缓存重建 |
| `test_core_pipeline.py` | 合并调用与回退、思考档按需升级（空答案/短答案/显式思考）、内联拼接跳过润色、OCR 兜底、OCR 归档（含求解失败仍留档）、`FILE:` 首行剥离与本地文件名、缓存按模型名失效、取消保留部分内容 |
| `test_solver_client.py` | 思考吃满配额后的降级、思考提前放弃、求解画像、配额与 effort 配置、网络重试与错误可诊断性、辅助链路关思考与 `AUX_TIMEOUT` |
| `test_vision_client.py` | 合并调用闸门（auto/true/false 与图片数上限）、**PAGE 分隔符协议**（NEW/CONT、跳号、重复页码、缺 `<<<END>>>`、LaTeX 反斜杠原样保留）、`refill_pages` 按页补做、三级回退链、流式收集器迭代期重试、输出截断告警 |
| `test_vision_provider.py` | 视觉层 provider 化：关思考下发的 payload、zhipu 回退逐字节一致、未知 provider 回落、缺密钥时报对环境变量名 |
| `test_answer_card.py` | 从解答中抽取「最终答案」小节的各种写法 |
| `test_verify.py` | 核对结果解析、verdict 归一化、一致性保护、异常兜底、模型名跟随 provider |
| `test_timings.py` | 阶段耗时聚合（含 `filename` 阶段）、DB 迁移、阶段缓存读写、转录三列与关键词检索、`/api/tasks?q=` |
| `test_retention.py` | 上传目录清理、孤立目录、图片缓存 LRU、**空保留集合拒绝删除**（数据丢失护栏回归） |
| `test_routes_upload.py` | 目录穿越防护、非法图片、体积限制、取消/重试门禁 |
| `test_file_monitor.py` | 文件稳定性等待、回调异常隔离、改名就位投递、临时文件过滤、去重账本、补偿扫描与句柄停止 |
| `test_image_order.py` | EXIF / 文件名 / mtime 三级时间来源、乱序图片重排、同时间稳定性、分组器入队顺序 |
| `test_resolve_verify_sse.py` | SSE 序号与 `Last-Event-ID` 续传、`/resolve` 与 `/verify` 门禁 |

> **新增用例时的注意**：`tests/conftest.py` 有一个 autouse 夹具，会把
> `UPLOAD_DIR` / `SOLUTION_DIR` / `DATA_DIR` / `DB_PATH` / `IMAGE_CACHE_DIR` / `OCR_DIR`
> 默认重定向到 `tmp_path`。这样"忘了 monkeypatch"也不会动到真实数据（2026-09-20 曾有
> 一次整批上传目录被清理的事故）。需要真实路径的用例请显式覆盖并说明理由。

### 前端（vitest + Testing Library）

```powershell
cd frontend
npm test
```

| 文件 | 覆盖内容 |
|---|---|
| `lib/utils.test.ts` | `formatTs` 对非法输入的防御（缺陷 D 白屏回归）、状态与题型标签 |
| `lib/streamBuffer.test.ts` | 流式缓冲上限与截断语义 |
| `features/stream/taskStream.test.ts` | 断线自动重连与退避、终态不再重连、用户手动重试、可见性恢复 |
| `stores/useTaskStore.test.ts` | SSE 事件 → 界面状态的映射（含答案卡、核对、取消） |
| `components/output/AnswerCard.test.tsx` | 答案卡渲染、复制、核对结论三种状态 |

新增功能时请同时补测试；修 bug 时优先写一个能复现的用例。

---

## 排障

### 页面空白 / 手机端看不到内容

1. 打开浏览器控制台看是否有报错。渲染期异常有 `ErrorBoundary` 兜底，
   若整页仍空白说明异常发生在边界之外
2. 访问 `http://<地址>:8000/api/health` 确认后端在跑
3. 访问 `http://<地址>:8000/api/status` 看监控与目录状态

### 手机能打开页面但"等待任务开始"

说明任务流（`/api/tasks/{id}/stream`）没连上。检查：

1. 手机与电脑是否在同一局域网；用 `http://<lan_ip>:8000` 而不是 `localhost`
2. Windows 防火墙是否拦截 8000 端口
3. 页面上是否出现"连接中断，正在重连（第 n 次）"提示——出现说明是连接问题，
   点「立即重试」

### 点了取消但一直在跑

正常情况下取消会在下一个阶段边界或流式分片之间生效，最长等待一个分片。
如果长时间无响应，检查日志中是否打印了"已请求取消任务"；没有则说明请求
没到后端（查看浏览器 Network 面板的 `POST /api/tasks/{id}/cancel`）。

### 产物写到了奇怪的位置

启动日志会打印实际使用的路径。默认 `ROOT_DIR` 是项目**父目录**；
在 `.env` 里设置 `SOLVER_ROOT_DIR` 可显式指定。

### 小字识别不准

调大最长边或提高 JPEG 质量（`.env`）：

```env
IMAGE_MAX_EDGE=2000
IMAGE_JPEG_QUALITY=90
```

### 出答案太慢

先看「设置」页的阶段耗时表确定瓶颈：

- `classify` / `ocr` 慢 → 通常是网络或图片过大，考虑调小 `IMAGE_MAX_EDGE`
- `solve` 慢 → 模型本身的推理时间，可关闭思考模式（`thinking=0`）
- `polish` 慢 → 多图任务会调用润色；提高 `MERGE_SKIP_THRESHOLD` 可更常跳过

### 磁盘占用增长

「设置」页会显示上传与解答目录占用。超出 `TASK_RETENTION_COUNT` /
`TASK_RETENTION_DAYS` 的任务会被清理（解答文件、上传目录、阶段缓存一起删）。
图片预处理缓存按 `IMAGE_CACHE_MAX_MB` 做 LRU。

`webapp/data/tasks.db`、`webapp/cache/` 都可以安全删除（会重建）。

---

## 目录结构

```
OnlineTest/
├── problem_solver_agent/     # 核心流水线（CLI + Web 共用）
├── webapp/                   # FastAPI 后端
│   ├── static/               # 前端构建产物（npm run build 生成）
│   ├── solutions/            # Web 端解答
│   ├── uploads/              # 任务原图
│   ├── cache/images/         # 图片预处理缓存
│   └── data/tasks.db         # 任务数据库
├── frontend/                 # React SPA 源码
├── tools/                    # 独立工具（截图/打字/遥控）
├── tests/                    # pytest 用例
├── docs/                     # 架构、接口、开发文档
├── .claude/CLAUDE.md         # 面向 AI 助手的项目说明
├── .env.example              # 环境变量样例
├── pytest.ini                # 测试配置
├── run_web.py                # Web 入口
└── start_web.bat             # Windows 一键启动
```
