# 自动化多图解题 Agent

**实时监控 → 智能分组 → 多模型协同，把连续截图自动变成结构化解答。**

一道长题目一张截图装不下时，本工具把你连续截的几张图自动归为一组，跑完
「题型分类 → 文字识别 → 合并润色 → 模型求解」的完整流水线，并实时把过程与
答案推送到浏览器（手机也能看）。

提供两条入口，共用同一套流水线实现：

- **Web 应用**（推荐）：FastAPI + React SPA，自动截图导入 + 手动上传，SSE 实时推送

前端技术栈：React 19 · TypeScript · Vite · Tailwind 4 · Zustand · TanStack Query ·
Radix UI · Sonner · markdown + KaTeX。
后端技术栈：FastAPI · SQLite · SSE · OpenAI SDK。
- **命令行 Agent**：只监控截图目录，处理完把 Markdown 写到 `solutions/`

---

## 快速开始

### 1. 环境准备

- Python 3.10+
- Node.js 20+（仅构建前端时需要）

### 2. 安装依赖

```powershell
pip install -r requirements.txt
```

### 3. 配置密钥

```powershell
copy .env.example .env
```

编辑 `.env`，至少填入两个密钥：

| 变量 | 用途 | 获取地址 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 求解模型 `deepseek-flash` + 辅助模型 `deepseek-flash` | platform.deepseek.com |
| `ZHIPU_API_KEY` | 视觉模型 `GLM-4.6V` 系列（分类 / OCR / 视觉推理） | open.bigmodel.cn |

完整可配置项见 `.env.example`（每项都有注释说明）。

### 4. 构建前端并启动

```powershell
cd frontend
npm install
npm run build     # 产物输出到 webapp/static/
cd ..
python run_web.py # 默认 http://localhost:8000
```

Windows 上也可以直接双击 `start_web.bat`（会做版本与依赖检查）。

### 5. 开始使用

1. 打开 `http://localhost:8000`
2. 用**静默截图工具**连续截图（见下），或直接在网页/手机上上传图片
3. 停止截图约 8 秒后自动分组并开始处理
4. 在「解题台」实时查看过程，答案生成后一键复制

> 用 PyCharm 开发时可直接选用项目内置的运行配置（含前后端一键启动），
> 详见 `docs/DEVELOPMENT.md` 的「在 PyCharm 中运行」。
>
> **⚠️ 网页模式（`start_web.bat` / `run_web.py`）和命令行 Agent（`python -m problem_solver_agent.main`）
> 是两个平级入口，都监听同一个截图目录，请只开一个**，否则同一张照片会被处理两次。
> 二者区别、手机 Syncthing 配置、图片顺序规则、产物位置等，见 **`docs/USER_GUIDE.md`**。

---

## 三种输入方式

| 方式 | 命令 / 入口 | 说明 |
|---|---|---|
| **手机自动同步** | Syncthing（见 `docs/USER_GUIDE.md` 第 4 节） | 手机拍完自动落到监控目录，推荐日常使用 |
| **热键静默截图** | `python tools/silent_screencapper.py`（需管理员权限） | 默认 `Alt+X`，用 GDI 直接抓屏，无闪烁。截图落到监控目录后自动触发 |
| **手机上传** | 网页右上角「手机扫码」 | 手机与电脑同一局域网，扫码后可直接传图/拍照 |
| **网页手动上传** | 解题台左侧 | 拖拽、点击选择或直接粘贴截图 |

配套小工具：

- `python tools/human_typer.py`（管理员）：接管 `Ctrl+V`，把剪贴板内容**模拟真人逐字输入**到当前窗口（考试客户端里"假装手打"）。
- `python tools/remote_trigger.py`：手机扫码遥控**电脑端截图**，绕开电脑上的键盘限制（端口 `REMOTE_TRIGGER_PORT`，默认 5555）。
- `python tools/diag.py`：一键自检（路径/密钥/Web/局域网 IP）。

> 答案生成后，除了网页，「手机文件管理器通过 SMB 直接打开 `solutions/` 看 Markdown」也是推荐方式——
> 完整步骤见 **`docs/USER_GUIDE.md`** 第 1.3 节。日常刷题建议只开命令行 Agent，
> 网页端留给事后复盘（两者不要同时开）。

---

## 目录与产物路径

默认情况下，工作根目录是**项目目录的父目录**（历史行为）：

```
<工作根目录>/                 # 默认 = OnlineTest 的上一级，可用 SOLVER_ROOT_DIR 覆盖
├── Screenshots/              # 截图监控目录（工具往里写，Agent 从这里读）
├── processed/                # 处理完的截图归档
└── solutions/                # Markdown 解答（方便文件管理器/Samba 查看）

OnlineTest/
├── webapp/solutions/         # Web 端解答（网页「任务」页读取这里）
├── webapp/uploads/           # 每次上传的原图（按任务 id 分目录）
├── webapp/cache/images/      # 图片预处理缓存（可安全删除）
└── webapp/data/tasks.db      # 任务数据库（可安全删除，会重建）
```

⚠️ 默认的 `ROOT_DIR` 会落在项目**外面**（例如 `D:\Users\wzw\Pictures`）。
如果你希望所有产物都留在项目内，在 `.env` 里显式指定：

```env
SOLVER_ROOT_DIR=D:\Users\wzw\Pictures\OnlineTest\workspace
```

启动时终端会打印实际使用的各个路径，便于确认产物去哪了。

---

## 界面说明

| 页面 | 用途 |
|---|---|
| **解题台** | 顶部状态条显示自动截图是否在跑；左栏任务时间线；右栏 **答案卡**（大字号、一键复制）+ 完整解答 + 实时进度（阶段耗时、字符数、取消/重试） |
| **任务** | 历史任务列表：状态、题型、图片数、总耗时；失败或已取消的任务可一键重试（复用已识别内容，不重复消耗额度） |
| **设置** | 监控目录与运行状态、磁盘占用、模型与密钥状态、阶段耗时统计、局域网地址与二维码 |

**手机端**：底部两个标签（题目 / 解答）。解答页支持双指缩放看图、一键复制答案、阅读模式（可调字号与暗色）。
自动截图产生新任务时只提示不抢屏，可自行决定是否切换（或打开「自动切换」）。

### 答案卡

考试时真正要"抄走"的是结论那几行，而不是整篇推理。所以后端会从解答里
抽取「最终答案」小节（规则抽取，不额外调用模型），前端渲染成独立的大字号卡片：

- **复制答案**：只复制结论，不带 markdown 符号
- **完整解答**：默认折叠，需要时展开
- **核对答案**（可选）：让第二个视觉模型对照原图复核，发现疑点时在卡片上方
  用醒目色列出问题与建议修正

### 重新求解

第一版答案不满意时，可以用「重新求解」菜单换策略再要一版，**跳过识别步骤**，
只需一次求解调用：

- 用最优解风格重解（`OPTIMAL`）
- 用讲解风格重解（`EXPLORATORY`）
- 关闭思考模式重解（更快）

---

## 性能与可靠性设计

这部分决定了「出一份答案要等多久」，值得了解：

| 机制 | 效果 |
|---|---|
| **发送前图片预处理** | EXIF 校正 → 最长边 1600 → JPEG q80，实测单张 2.96 MB → 187 KB（缩小约 16 倍），4 图请求从约 15 MB 降到约 1 MB |
| **分类 + 识别合并调用** | 一次视觉调用同时得到题型与逐页文本，省一轮往返与一次重复图片计费；`auto` 模式仅在单图时尝试（多图必然超长被截断，回退反而白等约 50 秒），解析失败自动回退 |
| **思考模式按需升级** | 先不开思考快跑一次（简单题十几秒出答案）；只有答案不合格（空 / 被截断 / 编程题答案过短）时才用「开思考 + 32000 token」重跑一次，第二次仍无正文就沿用第一版 |
| **漏检补偿扫描** | 监控目录每 15 秒（可配）扫一遍，兜住同步工具「改名就位」、watchdog 缓冲区溢出、进程停机期间到达的照片；去重账本保证只投递一次 |
| **按拍摄时间排序** | Syncthing 按数据块同步，落盘顺序是随机的；送进 OCR 前统一按 EXIF → 文件名时间 → mtime 重排，保证"第几张 = 第几页" |
| **回退路径并行** | 分类与逐页 OCR 并行执行，关键路径从「相加」变成「取最大」 |
| **按需润色** | 单图题直接采用识别原文；多图合并后文本较短也跳过润色（省一次 2–10 秒调用） |
| **识别失败兜底** | 部分页识别失败时改用原图直读求解，而不是整体失败 |
| **流式渲染两段式** | 生成过程用轻量文本展示，完成后再做一次 markdown + 公式排版，避免逐字重渲染卡顿 |
| **首屏体积** | 首屏 JS 约 512 KB（gzip 161 KB），markdown + KaTeX（430 KB）与设置页按需加载 |
| **真实取消** | 取消在阶段边界与流式分片之间生效，已生成内容保留为 `*.partial.md` |
| **重试复用缓存** | 分类/识别结果写入 `stage_cache`，重试直接从求解阶段开始 |
| **断线续传** | 每条 SSE 事件带序号，重连时按 `Last-Event-ID` 只补发漏掉的部分 |
| **并发上限** | `MAX_CONCURRENT_TASKS`（默认 2）避免高峰期把 API 配额打满导致全部超时 |
| **自动清理** | 超出保留数量或天数的任务，连同上传原图与缓存一起清理 |

相关参数都在 `.env.example` 里，按需调整即可（例如小字识别不准时把
`IMAGE_MAX_EDGE` 调到 2000，或把 `IMAGE_JPEG_QUALITY` 提到 90）。

---

## 使用命令行 Agent

```powershell
python -m problem_solver_agent.main
```

启动时会做一次健康检查（密钥 + 各求解器连通性），失败即退出。之后它只监控
`Screenshots/`，每组处理完把解答写入 `solutions/`，并同步一份到 `webapp/solutions/`。
CLI 与 Web 可以同时运行，共用同一套配置与流水线。

---

## 开发

```powershell
# 前端开发服务器（带 /api 代理到 8000 端口）
cd frontend && npm run dev

# 前端类型检查 / 代码检查 / 单元测试 / 生产构建
npm run build
npm run lint
npm test

# 后端测试（164 个用例，全部打桩，不产生真实 API 调用）
pip install -r requirements-dev.txt
pytest                    # 配置见 pytest.ini，用例在 tests/

# 一键自检：路径、密钥、服务可达性、图片压缩收益
python tools/diag.py
```

改动前端源码后**必须** `npm run build`，否则生产页面仍是旧产物。

详细架构、接口契约与排障说明见：

- **`docs/USER_GUIDE.md` — 完整使用说明（先看这个）**：两种运行方式的区别、Syncthing 手机同步配置、
  图片顺序与分组规则、产物位置、常见问题、配置速查
- `docs/ARCHITECTURE.md` — 分层、数据流、并发与取消模型
- `docs/API.md` — REST 与 SSE 事件契约
- `docs/DEVELOPMENT.md` — 开发环境、常用命令、常见问题排查
- `.claude/CLAUDE.md` — 面向 AI 助手的项目说明与约定

---

## 切换模型

`vision_client.py` 与 `solver_client.py` 都是 provider-agnostic 的，改模型只需动
`problem_solver_agent/config.py`：

```python
VISION_CLASSIFY_MODEL = "GLM-4.6V-FlashX"   # 分类 + OCR
VISION_REASONING_MODEL = "GLM-4.6V"         # 图形推理

SOLVER_CONFIG = {
    "deepseek": {"model": "deepseek-flash", "base_url": "https://api.deepseek.com/v1"},
}
SOLVER_ROUTING_CONFIG = {"CODING_SOLVER": "deepseek", "DEFAULT_SOLVER": "deepseek"}
```

新增 provider 后需在 `.env` 配置 `{PROVIDER}_API_KEY`（全大写），例如添加
`new_provider` 就要配 `NEW_PROVIDER_API_KEY`。

---

## 许可证

见 `LICENSE`。
