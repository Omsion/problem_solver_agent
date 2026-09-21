# 架构说明

## 总览

```
                    ┌────────────────────────── 输入 ──────────────────────────┐
  Alt+X 热键截图 → Screenshots/  ←─ watchdog 监控 ─┐                            │
  手机/网页上传  ────────────────────────────────→ webapp/uploads/<task_id>/    │
                                                    │                            │
                                    ┌───────────────┴───────────────┐            │
                                    │  ImageGrouper（时间窗口分组）  │            │
                                    └───────────────┬───────────────┘            │
                                                    ▼                            │
                        ┌───────────────────────────────────────────┐            │
                        │   SolutionPipeline（core，唯一实现）        │            │
                        │   预处理 → 分类+多图合并转录 → 拼接 → 求解   │            │
                        └───────────┬───────────────────┬───────────┘            │
                                    │                   │                        │
                    on_event 回调   │                   │ 写入 Markdown          │
                                    ▼                   ▼                        │
              ┌──────────────────────────┐      solutions/ + webapp/solutions/   │
              │ CLI: 日志 + 归档原图      │                                        │
              │ Web: TaskEventBus → SSE  │ ──→ 浏览器 / 手机                       │
              └──────────────────────────┘                                        │
```

## 分层

### `problem_solver_agent/` — 核心流水线（CLI 与 Web 共用）

| 模块 | 职责 |
|---|---|
| `config.py` | 全部配置常量。路径解析、图片预处理参数、保留策略、并发上限。支持 `.env` 覆盖 |
| `image_prep.py` | **发送前的图片预处理**：EXIF 校正 → 缩放 → JPEG 压缩 → 磁盘/内存缓存 |
| `image_order.py` | **题目图片排序**：EXIF `DateTimeOriginal` → 文件名时间戳 → mtime，保证"第几页"与拍摄顺序一致 |
| `vision_client.py` | 视觉调用：分类、多图合并转录（`<<<PAGE n\|NEW/CONT>>>` 分隔符协议，**一次请求带 N 张图**）、逐页 OCR（回退路径）、视觉推理。provider 化（`deepseek` 默认 / `zhipu` 回退）、重试、超时与截断诊断 |
| `solver_client.py` | 求解器调用：流式（含 reasoning 事件与求解画像）、思考配额/effort 配置、非流式分析、健康检查 |
| `core_pipeline.py` | **流水线编排**：阶段编排、事件发射、耗时统计、取消检查、答案卡抽取、思考档按需升级、OCR 归档落盘 |
| `cancel.py` | 取消令牌与 `CancelledError` |
| `answer_card.py` | 从解答文本中抽取「最终答案」小节 |
| `pipeline.py` | 共享纯函数：题型映射、求解器路由、Prompt 构建、配置校验 |
| `prompts.py` | 全部 Prompt 模板（模块级常量，**均为 raw 字符串**） |
| `image_grouper.py` | 时间窗口分组 + 工作线程池；把处理委托给 `core_pipeline` |
| `file_monitor.py` | watchdog 监控（`on_created` + `on_moved`）、文件稳定性等待、去重账本、补偿扫描；回调式，不反向依赖分组器 |
| `netcheck.py` | 本机地址判定与「是否远程手机」识别 |
| `utils.py` | 日志单例、文件名清理、题号提取 |
| `main.py` | CLI 入口：配置校验 → 健康检查 → 启动监控 |

### `webapp/` — Web 后端

| 模块 | 职责 |
|---|---|
| `app.py` | 应用工厂 + lifespan（路径报告、启动清理、客户端预热、启动监控） |
| `routes.py` | 全部 HTTP 路由（REST + SSE），模块级 `task_manager` / `pipeline_service` 在启动时注入 |
| `pipeline.py` | 把 core 流水线适配成 Web 事件流，落库状态/耗时/答案卡 |
| `models.py` | `TaskManager`：sqlite3 CRUD + 幂等迁移 + 阶段缓存 |
| `jobs.py` | `TaskRegistry`：取消令牌登记 + 并发信号量 |
| `presence.py` | `RemotePresence`：远程设备连接计数（断线重连不误报） |
| `events.py` 的等价物 | `TaskEventBus`（当前在 `routes.py` 内）：per-task 广播 + 全局广播 |
| `timings.py` | 阶段耗时聚合（P50/P90/均值/缓存命中率） |
| `retention.py` | 磁盘清理：上传目录、孤立目录、图片缓存 LRU |
| `auto_import.py` | 桥接监控目录与 Web 流水线；暴露运行状态 |
| `config.py` | Web 专属常量：目录、端口、上传限制 |

### `frontend/` — React SPA

| 目录 | 职责 |
|---|---|
| `src/api/client.ts` | 兼容层，转出 `lib/api` |
| `src/lib/api.ts` | 所有 HTTP 调用 + 统一的 `ApiError` |
| `src/lib/utils.ts` | `cn`、`formatTs`（防御非法时间戳）、`formatDuration` |
| `src/lib/taskStatus.ts` | 状态标签与终态判断 |
| `src/lib/problems.ts` | 题型枚举 → 中文标签 |
| `src/features/stream/taskStream.ts` | **唯一的 SSE 连接实现**：退避重连、可见性恢复、终态停止 |
| `src/stores/useTaskStore.ts` | 流式进度缓冲、连接注册表、全局 SSE、远程状态 |
| `src/stores/useUploadStore.ts` | 待上传文件队列 |
| `src/stores/useLayoutStore.ts` | 分栏比例、灯箱图片集合与下标 |
| `src/components/layout/` | AppHeader、SplitPanelLayout、MobileLayout、QrCodeButton |
| `src/components/output/` | OutputPanel、lazy（重渲染器懒加载边界）、MarkdownRenderer、ProgressSteps、ThinkingBlock、TimingBreakdown、ReadingMode |
| `src/components/tasks/` | TaskHistoryPage、TaskCard |
| `src/components/settings/` | SettingsPage |
| `src/components/ErrorBoundary.tsx` | 渲染期错误边界（避免整页空白） |

---

## 关键设计决策

### 1. 只有一份流水线实现

历史上 CLI（`image_grouper._execute_pipeline`）与 Web（`webapp/pipeline.py`）各写了
一份流程，并且已经出现行为差异：CLI 用 `.replace("{raw_texts}")`、Web 用 `.format()`
（题目里的 `{}` 会被当占位符）；CLI 把求解器的 reasoning 分片直接 join 掉。

现在统一到 `core_pipeline.SolutionPipeline`，两端只负责各自的**副作用**：

- CLI：把事件打到日志、处理完把原图归档到 `processed/`
- Web：把事件转发给 SSE、把状态与耗时落库

### 2. 图片预处理是最大的性能杠杆

实测单张 2288×1764 截图 2.96 MB，base64 后 3.9 MB；4 图请求约 15 MB。
`image_prep.prepare_for_api()` 做 EXIF 校正 + 最长边 1600 + JPEG q80，
压到 187 KB（base64 243 KB），**缩小约 16 倍**，对文字 OCR 无明显影响。

同时修掉一个隐性错误：旧实现把所有图片的 MIME 都写成 `image/jpeg`，
PNG 上传时格式标记与实际内容不符。

缓存键是「内容哈希 + 参数」，所以同一张图在分类、OCR、视觉推理、核对之间
只编码一次；跨进程复用靠磁盘缓存，进程内复用靠 64 条内存 LRU。

### 3. 视觉层：一次请求带完所有图，正文逐字直出

视觉层的迁移目标是 DeepSeek `deepseek-flash`（`VISION_PROVIDER=deepseek`，与求解共用
`DEEPSEEK_API_KEY`，本仓库 `.env` / `.env.example` 都显式启用）；`zhipu` 是长期保留的
一键回退分支（`GLM-4.6V-FlashX` 分类/OCR + `GLM-4.6V` 推理/核对），也是**代码在未配置
`VISION_PROVIDER` 时的默认值** —— 双 provider 的 A/B 判定（S1/S2）出数字之前，不把未
验证的 provider 变成默认。**两张 provider 表互相独立**，换视觉 provider 不会动求解。

调用链（8 图任务，`COMBINED_VISION_MAX_IMAGES=8`、`VISION_BATCH_SIZE=4` 时）：

```
截图 ×8 ──> 按 4 张一批切分（VISION_BATCH_SIZE），2 批**并发**（VISION_BATCH_WORKERS）
          └─> 每批 1 次 POST /chat/completions（extra_body={"thinking":{"type":"disabled"}}）
                └─> <<<TYPE>>> + <<<PAGE 1..4|NEW/CONT>>> + <<<END>>>
                      ├─ 解析全部成功 → vision_mode="batched"（seams=True，批首按 NEW）
                      │    └─ join_by_continuation() 本地拼接，润色调用 = 0（VISION_INLINE_MERGE）
                      └─ 部分缺页 → refill_pages() 只补失败的那几页（补 k 页 = k 次调用，
                                    按 OCR_PARALLEL_WORKERS 并行）
                            ↓ 某批连页标记都没有
                          JSON 协议（兼容保留）→ vision_mode="json"（seams=False → 保留润色）
                            ↓ 每批都失败
                          1 次分类 + N 次并行 OCR（OCR_PARALLEL_WORKERS=4）→ vision_mode="parallel"
```

**为什么分批而不是一次带完 8 张**：一次请求带 N 张图时，模型是在**单序列**上逐页生成
（8 图实测 8.4 s）；拆成 2 批并发后同样的 8 张只要 6.4 s（同轮对照，两轮都成立），
而图片 token 仍只上传一次、批内 NEW/CONT 去重照旧。跨批的接缝没法判断，因此每批首页
按 `NEW` 保守处理。完整数据见 `docs/plans/deepseek_vision_migration.md` §8.2/§8.5。

**为什么必须同时换协议**：旧的 JSON 协议要求把全部转录塞进一个 JSON 字符串，而
`\frac` / `\begin` / `\theta` / `\neq` 漏转义时 `\f` `\b` `\t` `\n` 都是**合法**转义 ——
解析"成功"、页数与长度校验全过，正文却被静默换成了控制字符，直接写进解答文件且无人发现。
分隔符协议下正文**逐字直出，不经过任何转义层**；页边界显式声明且按模型声明的序号（1 基）
定位，跳号/重排能被直接发现并记入 `failed_pages`，部分成功也能用，不必整批作废。

**思考模式必须显式关闭**：DeepSeek 的思考模式默认开启且 effort=high，
不显式下发 `thinking.type=disabled`（`VISION_DISABLE_THINKING=true`）时，分类/OCR
这类短任务的正文会被思考过程挤空 —— 与求解器踩过的是同一个坑。

### 4. 8 图任务的调用账：9 → 2 与 12 → 3~4

| 环节 | 优化前 | 优化后 |
|---|---|---|
| 分类 | 1 | ——（并入合并调用） |
| 逐页 OCR | **8**（`OCR_PARALLEL_WORKERS=1`，串行） | **0**（合并调用按批带图） |
| **视觉请求数** | **9** | **2**（`VISION_BATCH_SIZE=4` → 8 图分 2 批并发） |
| 润色 | 1（整篇重写，输入输出各约 10K token） | **0**（合并成功即内联拼接，`timings.polish == 0`） |
| 文件名生成 | 1（一次隐形调用：不计时、不计费、界面不可见） | **0**（`FILENAME_MODE=auto`：求解正文首行 `FILE:` → 本地题号 → 才调模型） |
| 求解 | 1（流式） | 1（流式，不变） |
| 缺页补做 | —— | 0–N 次（实测 2 次真实分批调用各缺 1 页 → 各补 1 次） |
| **API 调用总数** | **12** | **3–4** |

> **调用次数是确定结论**（配置与代码路径决定，可用日志与 `_probe/probe_e2e_real.py` 验证）。
> **耗时以实测为准**：分批合并 8 图 ≈6.4 s（同轮对照），一次带完 ≈8.4 s，并行回退 3.7–8.5 s
> （服务端负载差异明显）。`VISION_BATCH_SIZE` / `VISION_BATCH_WORKERS` 是这两个数的旋钮，
> 完整数据见 `docs/plans/deepseek_vision_migration.md` §8.2。

**回退与降级都不会整批作废**：缺页按页补做；`<<<TYPE>>>` 也缺失时才补一次分类；
`CONT` 标记缺失或非法按 `NEW` 保守处理（宁可多花一次润色，也不能丢掉一道题的开头）。

### 5. 取消是协作式的

`CancelToken` 是一个线程安全的布尔标志，检查点设在：

- 每个阶段开始时（`cancel.raise_if_cancelled()`）
- 流式求解的**每个分片之间**

第二点决定了取消的响应速度上限：用户点取消后最多再等一个 token 分片。
取消后已写入的内容会改名为 `<task_id>.partial.md` 保留，而不是删掉。
OCR 归档在**视觉阶段一结束就落盘**（不等成功收尾），所以取消与求解失败也保得住识别结果。

### 6. 并发与背压

`TaskRegistry` 用一个 `BoundedSemaphore(MAX_CONCURRENT_TASKS)` 限制同时运行的任务数。
没有这个限制时，自动导入高峰期会让多个任务同时打满上游配额，结果是**所有任务
一起超时**——比慢更糟。

取消令牌也存在同一个注册表里，是"任务是否在跑"的唯一真相来源。

### 7. 阶段缓存让重试几乎免费

分类与逐页 OCR 的结果按 `(task_id, stage)` 写入 `stage_cache` 表，**合并与并行两条路径
都走缓存**。重试一个失败任务时，`_cached_or_compute` 会直接命中缓存，跳过视觉调用，
只重跑求解。命中情况会出现在耗时明细的 `cached` 数组里。

缓存 payload 里**记了模型名**（`{"_meta": {"model": ...}, "value": ...}`），读取时比对：
provider 切换后旧缓存**自动失效**，不需要改表结构，也不会把 DeepSeek 写下的转录当成
智谱的结果复用。

### 8. 前端首屏体积与渲染

`react-markdown` + `remark/rehype` + KaTeX 约占 430 KB。这些只在真正要看解答
时才需要，所以放进 `components/output/lazy.tsx` 的动态分包；设置页也按需加载。

另外，**流式过程中不使用完整的 markdown 渲染**：直接以等宽/段落文本展示，
只在收到 `done` 之后才交给 markdown + KaTeX 做一次完整渲染。这避免了
"每个 token 都全量重跑解析与公式排版"带来的卡顿。

当前产物：首屏 `index-*.js` 约 512 KB（gzip 161 KB），`MarkdownRenderer` 430 KB
按需加载。历史单包为 785 KB 且无分割。

### 9. 答案卡：把"结论"从长文里提出来

考试场景真正要的是那几行结论，而不是整篇推理。后端在解答生成后用规则
（`answer_card.py`，不额外调用模型）抽取「最终答案」小节，随 `done` 事件下发；
前端渲染成独立的大字号卡片，配一键复制。

抽取失败时回退到首段内容，并在卡片上标注"自动提取"，前端不会假装它是精确结论。

### 10. 核对模式：默认关闭的第二模型复核

`verify.py` 用视觉模型对照原图复核答案，输出 `agree` / `disagree` / `unclear`。

三个设计约束：

- **不改变默认速度**：由用户点击触发，不参与默认流水线
- **不修改已有解答**：结果只追加到文件末尾的小节，界面单独展示
- **保守**：模型声称有错但没给出任何问题或修正时降级为 `unclear`，
  避免一次误判把正确答案吓成"错误"

### 11. 换路重解：跳过 OCR 的第二版答案

第一版答案不满意时，用户往往只需要"换个风格再解一次"。`/resolve` 复用解答
文件里记录的题目文本（或阶段缓存），只重跑求解，因此通常几秒到几十秒就能
拿到第二版，而不是重新走一遍完整流水线。

### 12. 远程设备识别

旧逻辑是 IP 黑名单：

```python
is_client_remote = client_host not in ("127.0.0.1", "::1", "localhost", lan_ip)
```

凡是不在列表里的本机地址都会被当成手机；电脑端打开二维码弹窗触发的
`/api/qrcode` 请求因此被误判为"手机已连接"，导致扫码按钮自己消失。

现在改为两个**正面条件同时成立**（`netcheck.is_remote_device`）：

1. 来源 IP 不属于本机（回环段、链路本地、本机所有网卡地址都不算远程）
2. User-Agent 像移动设备

并且 `RemotePresence` 用引用计数处理断线重连，只有计数归零才广播"已断开"。

---

## 数据流：一次自动导入

1. `silent_screencapper.py` 把 PNG 写入 `Screenshots/`
2. `file_monitor.ImageEventHandler.on_created` 触发，先 `wait_until_stable` 等写盘完成
3. `ImageGrouper.add_image` 重置 8 秒定时器；超时后把图片组推入队列
4. `WebAutoImporter._web_pipeline_wrapper`：复制图片到 `uploads/<task_id>/` →
   建任务记录 → 广播 `auto_imported` → 调用 `PipelineService.run`
5. `SolutionPipeline` 执行：预处理图片 → 分类 + 多图合并转录（1 次请求；合并成功即内联拼接，
   跳过润色）→ 流式求解
6. 视觉阶段一结束就写 OCR 归档 `<OCR_DIR>/<日期>/<task_id>.md`（取消/求解失败也保得住）
7. 每个事件经 `on_progress` 进入 `TaskEventBus`，被 SSE 推给所有订阅者
8. 完成后写解答文件（frontmatter 带 `task_id` / `ocr_archive`）、更新任务状态与耗时
   （含 `problem_text` / `ocr_raw_text` / `vision_mode`）、同步一份到 `ROOT_DIR/solutions`

---

## 已知限制

- **单进程假设**：SQLite + 内存事件总线，不支持多 worker 部署（`uvicorn --workers > 1`）
- **不上全异步**：OpenAI 同步 SDK 的阻塞调用无法被真正取消；全异步需要连 SDK
  一起换，收益不匹配风险，因此当前用线程池 + 信号量控制
- **回放窗口有限**：每个任务只保留最近 500 条事件用于断线续传，超出后
  重连会从头收（前端对同一事件做了幂等处理）
- **核对模式依赖原图**：原图被保留策略清理后无法核对（返回 `upload_expired`），
  若常用核对，可适当调大 `TASK_RETENTION_DAYS`
- **前端未做视觉回归测试**：现有测试覆盖逻辑与关键组件行为，不含截图对比
- **合并调用有图数上限**：超过 `COMBINED_VISION_MAX_IMAGES`（默认 8）的图组退回
  「分类 + 并行 OCR」，调用次数重新变多；8 张以内的图组按 `VISION_BATCH_SIZE`（默认 4）
  **分批并发**（跨批接缝按 NEW 保守处理，不做跨批去重）
- **单一供应商**：视觉与求解都在 DeepSeek 上，任一侧不可用即整条流水线不可用 ——
  这是保留 `zhipu` 回退分支的主要理由（回退成本只是一个环境变量）
- **历史搜索用 LIKE 而非 FTS5**：`TASK_RETENTION_COUNT=100` 是百行表，LIKE 微秒级；
  FTS5 默认分词器对中文无效，要 `tokenize='trigram'` 还得维护虚表与同步逻辑。表涨到万级再换
- **隐私未被改善**：截图以 base64 发送，服务端解码后模型看到的就是完整原图；
  换 provider 只是换接收方。唯一根治手段是本机视觉模型

---

## 迭代记录

| 轮次 | 主要内容 |
|---|---|
| 第一轮 | 修复 4 个已记录缺陷；后端结构分层与契约；core 流水线收敛；图片预处理提速（请求体积缩小约 16 倍）；真取消与重试；保留策略；文档与 116 个后端测试 |
| 第二轮 | 前端技术栈升级（radix-ui / sonner / lucide-react / @tanstack/react-query）；答案卡；核对模式；换路重解 `/resolve`；SSE `Last-Event-ID` 续传；vitest 前端测试 54 个 |
| 第三轮 | 视觉层迁移到 DeepSeek `deepseek-flash`（`VISION_PROVIDER`，`zhipu` 一键回退）；多图从 8 次串行 OCR 改为 1 次合并调用（`<<<PAGE n\|NEW/CONT>>>` 协议 + 缺页补做 + 三级回退）；合并成功内联拼接跳过润色；视觉与辅助链路显式关闭思考；OCR 逐页归档 + 解答 frontmatter 加 `task_id` / `ocr_archive`；文件名生成双模式（`FILENAME_MODE`）；`tasks` 三列 + `q` 关键词搜索；单价表与 `TOKENS_PER_IMAGE` 修正；新增 `tools/vision_ab.py` A/B 工具 |
