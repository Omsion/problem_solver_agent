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

## 代码约定

- 所有 Python 函数**必须有类型提示**（项目约定）
- 代码注释用中文说明**为什么**（背景、坑点），不要复述"做了什么"
- 路径一律用 `pathlib.Path`，不要字符串拼接
- Prompt 模板必须是 **raw 字符串**（`r"""..."""`），否则 LaTeX 里的 `\begin`
  等会被当成非法转义序列
- 前端 import 顺序：外部库 → 内部模块 → 类型
- 新增接口后同步更新 `docs/API.md` 与 `frontend/src/types/index.ts`

---

## 测试

用例在 `tests/`，全部使用打桩，**不会产生真实 API 调用**。

| 文件 | 覆盖内容 |
|---|---|
| `test_netcheck.py` | 本机地址判定、移动端 UA 识别（远程连接误报回归） |
| `test_remote_stream.py` | 全局 SSE 端点、`RemotePresence` 计数与断开通知 |
| `test_image_prep.py` | 缩放边界、RGBA 白底、EXIF 方向、缓存命中、损坏缓存重建 |
| `test_core_pipeline.py` | 合并调用与回退、跳过润色、OCR 兜底、取消保留部分内容、阶段缓存 |
| `test_answer_card.py` | 从解答中抽取「最终答案」小节的各种写法 |
| `test_timings.py` | 阶段耗时聚合、DB 迁移、阶段缓存读写 |
| `test_retention.py` | 上传目录清理、孤立目录、图片缓存 LRU |
| `test_routes_upload.py` | 目录穿越防护、非法图片、体积限制、取消/重试门禁 |
| `test_file_monitor.py` | 文件稳定性等待、回调异常隔离 |

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
