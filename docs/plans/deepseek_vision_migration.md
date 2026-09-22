# 视觉层迁移：GLM-4.6V → DeepSeek V4.1 Flash（`deepseek-flash`）

> **文档定位**：可执行实施方案。架构与决策见 `../ARCHITECTURE.md`；接口契约见 `../API.md`。
> 本文的模型事实来自 2026-09-19 抓取的 DeepSeek 官方文档（`api-docs.deepseek.com`），
> 非估计值；价格同步自官方「模型 & 价格」页。

---

## 0. 结论先行

### 0.1 先纠正一个前提：本方案**不解决隐私问题**

提出迁移的原始动机是「担心图片隐私」。必须写清楚：

**Base64 是编码，不是加密。** 图片以 `data:image/jpeg;base64,...` 发送，厂商服务端
收到后的第一步就是解码回原始像素。模型看到的就是**完整原图**，没有任何模糊、
遮罩或保护——与屏幕上所见一致。本轮迁移只是把同一张原图从**智谱**的服务器
搬到 **DeepSeek** 的服务器：**风险没有消除，只是换了接收方**。

真正能降低隐私暴露的只有三条路（按有效性排序）：

| 手段 | 有效性 | 代价 |
|---|---|---|
| 本地视觉模型（Qwen2.5-VL / MiniCPM-V / InternLM-XComposer 本地部署） | **根治**：图片不出本机 | 需要 GPU 显存 + 部署运维；OCR 质量需自测 |
| 发送前在本机对敏感区域打码 / 裁剪（`image_prep.py` 里插一步） | 显著：个人信息不上传 | 需要区域检测，会伤及题目内容 |
| 选用有明确数据保留政策、可关闭「用于训练」的厂商 | 有限：降低二次使用风险 | 不等于不泄露 |

项目**已经**有一个真实有效的隐私收益，但它是既有能力、不是本次迁移带来的：
`image_prep.py` 的 EXIF 校正会**丢弃 EXIF 元数据**（GPS 坐标、设备型号、拍摄时间）。
这保护的是元数据，**图片内容一个像素都没少**。

### 0.2 本次迁移的真实收益与代价

**收益（真实存在，但和隐私无关）**

1. **单一供应商 / 单一密钥 / 单一 base_url**：整个项目只依赖 DeepSeek 一家，
   失效面减半，`.env` 少一个必填项；
2. **调用形态完全一致**：DeepSeek 的图片块就是 OpenAI 标准的
   `{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}` —— 与
   `vision_client._build_user_content` **现有代码逐字相同**，迁移不需要新抽象；
3. **可复用的请求结构**：视觉与求解走同一个模型（`deepseek-flash`），
   prompt 风格、错误处理、重试策略可以统一；
4. **1M 上下文 + 384K 输出**：远大于 GLM 系列的 8192 输出上限。**这是本次迁移最大的
   技术收益** —— 它解除了 `COMBINED_VISION_MAX_IMAGES=1` 背后那个根因（见 0.4 节）；
5. **`user_id` 隔离**：DeepSeek 支持 `extra_body={"user_id": "..."}`，官方说明该字段用于
   **内容安全隔离 + KVCache 隔离 + 调度隔离**。项目的用户 ID（`u_xxxxxxxx`，
   见 `webapp/accounts.py`）已满足 `[a-zA-Z0-9\-_]+` 格式，可直接透传。

**理论收益（本项目场景下几乎用不上，不要当成迁移理由）**

- **缓存命中价极低**：空闲时段 0.02 元/百万 token。DeepSeek 的上下文硬盘缓存确实
  **默认开启、隐式生效**，但本项目每次提交的都是**全新图片**，图片部分无法命中；
  全仓也**没有任何**缓存相关配置（搜 `prompt_cache|cache_control|prefix_cache`
  零命中）。唯一有意义的做法是保证 prompt 模板在前、图片在后
  （`vision_client._build_user_content` 已是这个顺序），收益极小。
  另外，缓存前缀单元要求**完整匹配**才命中，而本项目的两次调用（视觉、求解）
  prompt 结构完全不同，不存在可复用的公共前缀。

**代价（必须先量化，不能拍脑袋）**

| # | 代价 | 说明 |
|---|---|---|
| C1 | **单价上涨** | OCR 从 `GLM-4.6V-FlashX`(0.5/1.5) 换到 `deepseek-flash`(1–2 / 4–8)，输入贵 **2–4 倍**，输出贵 **2.7–5.3 倍** |
| C2 | **图片被二次缩放** | DeepSeek 会把每图缩到「约 1300×1300 等效总像素」，每图 ≤1024 token。当前 `IMAGE_MAX_EDGE=1600` 送出去的图会**再被缩一次**，小字/公式 OCR 可能退化 |
| C3 | **思考模式默认开启** | 官方：「思考模式默认打开，且 effort 默认为 high」。不显式关闭的话，分类/OCR 这类短任务会被思考过程吃光配额 |
| C4 | **采样参数失效** | 「思考模式不支持 temperature / presence_penalty / frequency_penalty」「top_p 仅思考模式生效，范围 0.95–1.0，低于 0.95 按 0.95 处理；非思考模式恒为 1.0」 |
| C5 | **单点依赖** | 迁移后 OCR / 润色 / 求解 / 核对**全部**依赖 DeepSeek 一家。现状是智谱挂了还能求解，迁移后任一环节不可用即全流程不可用。这是保留 `zhipu` 回退分支的主要理由 |
| C6 | **成本归因变难** | 视觉与求解同为 `deepseek-flash`，账单上只能靠 `stage` 字段区分。`core_pipeline._emit_usage` 已正确标注 stage，但 `_generate_filename` 的调用**完全没进入用量统计**（见 0.4 节 T4） |

> **关于并发**：DeepSeek 的并发限制是 **2500**（`deepseek-flash`）/ 500（`deepseek-v4-pro`），
> 账号粒度、与 API Key 无关，定义为「一个请求从发出到响应完成记为一个并发」。本项目
> `MAX_CONCURRENT_TASKS=2`、OCR 实为串行 —— **2 vs 2500，差三个数量级，并发从来不是约束**。
> 把「视觉与求解共用同一模型」写成并发代价是**错的**，真实代价是上面的 C5 与 C6。

因此本方案不是「连根拔起」，而是 **「先用 A/B 量化 C1/C2，再带闸门灰度切换，随时一键回退」**。

### 0.3 一句话设计原则

> **保留 `VISION_PROVIDER` 开关（`deepseek` / `zhipu`），默认值已切到 `deepseek`** ——
> 闸门条件（§6 第 5 步）已满足：2026-09-21 双 provider 的 A/B 判定通过（题目正文要素缺失 0、
> 逐图分类一致率 97.2%、截断 0、LaTeX 损坏 0，见 §8.6）。任何一项回归就回退，
> 回退只需改一个环境变量（`VISION_PROVIDER=zhipu`）。**

### 0.4 多图场景的提速设计（本节是迁移的主要动力）

**典型场景**：一个分组窗口（`GROUP_TIMEOUT=8s`）内会有 **5–8 张图**，可能是同一题的
多页，也可能是 7–8 道互不相关的题。

**现状的调用账（8 图任务）**：

| 环节 | 调用数 | 说明 |
|---|---|---|
| 分类 | 1 | `classify_problem_type` |
| **逐页 OCR** | **8** | **串行** —— `OCR_PARALLEL_WORKERS=1`（`config.py:196`） |
| 润色 | 1 | 合并文本 ≥1200 字符必调 |
| 文件名生成 | 1 | `_generate_filename`，**不计时、不计费、用户看不见** |
| 求解 | 1 | 流式 |
| **合计** | **12 次** | 其中视觉调用 9 次 |

**延迟主体是那 8 次串行 OCR**：按项目自己实测的单张 3.5–16.3 s
（见 `watermark_removal.md`），就是 **28–130 秒**的纯等待。这才是端到端延迟的大头，
比省一次分类调用重要得多。

#### 提速项清单（按收益排序）

| # | 改动 | 现状 | 改后 | 8 图收益 |
|---|---|---|---|---|
| **T1** | 多图合并调用 | `COMBINED_VISION_MAX_IMAGES=1`，多图**连请求都不发**（`vision_client.py:320-327`） | 提到 **8**，并换协议（见下） | 视觉调用 **9 → 1** |
| **T1b** | 分批合并 + 批间并行（组 H2，**2026-09-21 实测后追加**） | 一次请求带 8 张 = 单序列串行生成（实测中位数 8.4 s） | 每批 ≤`VISION_BATCH_SIZE`(4) 张、批间并发 | 视觉调用 **1 → 2**，但耗时 **8.4 s → 6.4 s**（同一轮实测），且图片 token 仍只付一次 |
| **T2** | OCR 并行度 | `OCR_PARALLEL_WORKERS=1`（串行） | **4**（落地值；设计稿曾写 3，见组 H） | 回退路径 8 次串行 → 2 轮（实测 12.6–19.8 s → 3.4–9.0 s） |
| **T3** | 润色条件触发 | 多图且 ≥1200 字符必调 | 按 `layout` 分流 | 独立题时省 1 次调用 |
| **T4** | 文件名生成 | 每次任务 1 次 deepseek 调用 | 本地由题号生成 | 省 1 次调用 |
| **T5** | 辅助链路关思考 | `ask_for_analysis` **没传** `extra_body`，思考默认开着 | 显式关闭 | 省下每次润色/命名的思考时间 |

> **T1b 与 T1 的关系（本节原文的"9 → 1"已被实测改写）**：T1 把"多图不发请求"变成
> "多图发 1 次请求"，而 2026-09-21 的 8 图真实对照显示**一次带 8 张是这条链路上最慢的
> 形态之一**（单序列串行生成）。因此落地形态是 T1 + T1b：**分批并发**，8 图 = 2 次请求、
> 耗时低于一次带完、图片 token 仍只付一次。数据与保留的并行回退对照见 §8.2 / §8.5。

#### T1 详述：为什么必须**同时换协议**

`tests/test_vision_client.py:1-11` 的头注释记录了 2026-09-13 的事故复盘：

> 合并调用要求把**所有图片的完整转录**塞进一个 JSON，而输出上限只有 8192 token；
> 多图时必然被截断 → 解析失败 → 回退，白等约 50 秒且这次调用照样计费。
> webapp 用量流水显示 **12 次尝试只成功 1 次**。

**GLM 的 8192 输出上限是原因之一，但不是全部** —— 更根本的问题是
**JSON 与 LaTeX 天然互斥**，而这个缺陷一直被「截断」的叙事掩盖着：

| 转录里的 LaTeX | 模型漏转义后的解析结果 | 后果 |
|---|---|---|
| `\frac{a}{b}` | `\f` 是**合法**的 formfeed 转义 | **解析"成功"**，文本变成 `␌rac{a}{b}` |
| `\begin{matrix}` | `\b` 是**合法**的 backspace 转义 | **解析"成功"**，正文里被插入退格符 |
| `\theta` | `\t` 是**合法**的 tab 转义 | **解析"成功"**，正文里被插入制表符 |
| `\neq` | `\n` 是**合法**的换行转义 | **解析"成功"**，公式被拆成两行 |
| `\sqrt` | `\s` 不是合法转义 | 硬失败 → 整批回退 |

**前四行是静默损坏**：它们通过了页数校验、长度校验，直接写进解答文件的
`# 题目文本` 小节，**没有任何机制会发现**。这比「解析失败」危险得多 —— 失败至少会回退。

另外三个结构性失败源：

- **裸换行**：转录是多行文本，JSON 字符串里每处换行都必须写成 `\n`，漏一处即硬失败；
- **截断**：输出撞 `max_tokens` → 字符串未闭合 → 失败；
- **页数不匹配且无顺序校验**：模型把两张连页合并、把纯图页省略、或把一张图拆成两条
  —— `vision_client.py:343` 只校验 `len(pages) == len(image_paths)`，
  **不校验顺序**，模型重排页面会被静默接受，而顺序决定"哪张是第一页"，错了整道题就废了。

因此 T1 必须配套换协议。**设计目标：转录正文逐字直出，不经过任何转义层；
页边界显式标记；部分成功可救。**

```
<<<TYPE>>>MULTIPLE_CHOICE
<<<PAGE 1|NEW>>>
（第 1 张图的完整文本）
<<<PAGE 2|CONT>>>
（第 2 张图的文本；若它与第 1 页是同一道题的延续，省略与上一页重复的内容）
<<<PAGE 3|NEW>>>
（第 3 张图的完整文本）
<<<END>>>
```

- 页正文 = 第 k 个标记结束位置 → 第 k+1 个标记起始位置（或 `<<<END>>>`，或 EOF）；
- 按模型声明的 **n**（1 基）定位而非出现顺序，**顺序错乱因此可被发现**（n 跳号 → 记入 `failed_pages`）；
- `NEW` / `CONT` 标记**顺带解决了 T3 的版面判断**，而且比单一的 `LAYOUT` 字段更强 ——
  它支持**混合场景**（前 3 页是同一题、后 5 页是独立题），单一字段做不到；
- 标记前的客套话、代码围栏一律丢弃，`parse_json_response` 里"找第一个 `{`"的脆弱兜底
  （LaTeX 的 `\{` 会让它切错边界）不再需要。

**回退链**（把「整批作废」降级为「按页补齐」）：

```
PAGE 协议解析 → 部分成功即采用，缺页走 refill_pages 单页补做（补 k 页 = k 次调用）
  ↓ 完全无法解析
JSON 协议（保留兼容）
  ↓ 失败
1 次分类 + N 次 OCR 并行路径（T2 的并行度在此生效）
```

`refill_pages(image_paths, result, failed_pages)` 只对失败下标重跑单页 OCR 并写回，
**补 2 页 = 2 次调用，而不是整批 9 次重来**。只有 `<<<TYPE>>>` 也缺失时才补一次分类。

**流式是必须的，不是可选项**：`stream=False` 时整段生成必须在单个超时内完成，
而合并调用要输出 6–16K token，非流式几乎必然超时。`_call_vision_api` 已支持
`stream=True`，需要新增一个收集器把流拼回字符串并取 `finish_reason`。

> **坑**：现有重试循环（`vision_client.py:132-172`）只覆盖 `create()` 抛出的异常；
> 流式下 `create()` 已经返回，错误发生在**迭代期间** —— 必须在收集器里补一层重试，
> 否则一次网络抖动就会丢掉整次转录。

#### T3 详述：润色在多题场景下**不仅冗余，而且有害**

**关键发现**：`TEXT_MERGE_AND_POLISH_PROMPT`（`prompts.py:71-101`）的 CoT 第 2 步
写死了「找到最长的重叠部分」，第 4 条核心原则又要求「**必须**识别并完美处理片段间的
重叠内容」。

**在「7–8 道互不相关的题」这个场景下，这两条指令是有害的**：相邻页之间必然存在
「下列哪项正确」「（ ）」这类相同措辞，模型被明确要求「找到最长重叠并丢弃」，
于是会**静默删掉一道题的开头**。

这是一条**今天就在生效、且没人发现的质量缺陷** —— 因为润色结果直接写进
`# 题目文本` 小节，而没人会拿它跟原图逐字比对。

**做法（两件事都要做）**：

1. **合并调用成功时，内联掉润色**：`<<<PAGE n|CONT>>>` 标记就是模型给出的接缝判断，
   prompt 对 `CONT` 页明确要求「省略与上一页重复的内容」。本地用一个纯函数
   `join_by_continuation(pages, continuations)` 拼接（`NEW` 用 `\n\n`，`CONT` 用 `\n`），
   **跳过润色调用**，成本是 **0 个额外 token**。

   省下的不只是时间：润色要**重新生成整篇合并文本**（输入输出各约 10K token），
   是整条流水线里最贵的一次输出，且 100% 与转录内容重复。

2. **并行路径下保留润色，但必须修 prompt**：在 `TEXT_MERGE_AND_POLISH_PROMPT` 前部
   新增一段「场景判断」，要求模型**先**判断片段之间是「同一题多页连续截图」
   还是「互不相关的多道题」，属后者时**绝不跨片段删除任何内容**，只做公式规范化。
   这一条对 >8 图或合并失败的回退路径是刚需。

`VISION_INLINE_MERGE`（默认 `true`）保留为回退开关：若发现模型在 `CONT` 页偷懒多删了
内容，置 `false` 即可回到「合并调用 + 独立润色」。而因为**原始 OCR 已按页归档**
（组 I），可以直接比对发现问题。

#### T4 详述：一次隐形的调用

`_generate_filename`（`core_pipeline.py:787-805`）每次都调一次
`solver_client.ask_for_analysis`（deepseek），而且**游离在 `StageTimings` 与
`UsageReport` 之外** —— 用户既看不到它的耗时，也看不到它的费用。

做法：优先用已有的 `extract_question_numbers` + `format_number_prefix`
（`utils.py:79-114`，本来就是它的 fallback 路径）本地生成文件名，仅在解析不到题号时
才调模型；同时把耗时与用量补进 `StageTimings` / `UsageReport`，让它在界面上可见。

#### T5 详述：辅助链路的思考一直是开着的

这条与「迁移」正交，但它是**当前最大的单点延迟浪费**，改动极小。

`config.AUX_PROVIDER = "deepseek"` + `AUX_MODEL_NAME = "deepseek-flash"`
（`config.py:55-56`）—— **润色与文件名生成本来就跑在 DeepSeek V4.1 Flash 上**。
而 `solver_client.ask_for_analysis`（`solver_client.py:359-389`）这样发请求：

```python
client.chat.completions.create(
    model=model, messages=messages, stream=False, temperature=0.7, timeout=120.0
)
```

**没有 `extra_body`。** 按官方「思考模式默认打开，且 effort 默认为 high」，
这两次调用**一直在跑 high effort 思考**：模型在输出润色结果/文件名之前，
先做一遍高强度思考，纯延迟浪费；`temperature=0.7` **静默失效**；思考消耗的 token
照样计费，而结果被直接丢弃（只取 `message.content`）。

修法与 `solver_client._build_payload` 里已有的做法完全一致
（`solver_client.py:104-108`，其注释记录了"关闭思考必须**显式**下发"的踩坑经验）。

**同一处还有第二个问题**：`ask_for_analysis` 内部**硬编码 `timeout=120.0`**
（`solver_client.py:371`）。而润色要把整篇合并文本重写一遍（输出 6–10K token），
**在思考模式下很可能撞上 120 s 超时**，然后按 `MAX_RETRIES=3` 指数退避重试
（10s → 20s → 40s）—— **一次润色最坏可能耗掉几分钟**。

这是当前延迟长尾的最大来源，比 OCR 本身严重。关掉思考能大幅降低命中概率，
但根治要给 `ask_for_analysis` 一个可配的超时（如 `AUX_TIMEOUT`，默认提到 300 s）。

#### 8 图场景的调用账（诚实版）

| | 现状 | 优化后 |
|---|---|---|
| 视觉调用次数 | **9 次**（1 分类 + 8 串行 OCR） | **2 次**（分批合并：8 图 → 2 批并发；见 §8.5） |
| 视觉关键路径 | 8 次串行 = **8 次 TTFT + 生成** | 2 批并发 = 1 批的 TTFT + 生成 |
| 润色 | 1 次（思考开着，**最坏撞 120 s 超时并重试 4 次**） | **0**（已内联） |
| 文件名生成 | 1 次（思考开着） | **0**（并进求解首行） |
| 求解 | 1 次流式 | 1 次流式（不变） |
| **API 调用总数** | **12 次** | **2–3 次**（2 批合并 + 1 求解；缺页补做另计） |

**必须讲清楚的一点：不能断言合并调用一定比并行路径快。**

合并调用是**单序列串行生成**，耗时 ≈ `N × T / R`；而修好并行度后的回退路径是
`ceil(N / W) × T / R`。当 `W ≥ N` 时，并行路径在生成时间上反而快 `N / W` 倍。
把合并调用当成"必定更快"是错的。

合并调用真正**确定**的收益是三条：

1. **图片 token 只计费一次** —— 分类那一趟的 8×1024 输入 token 从"白付"变成合并进同一次请求；
2. **少 8 次 TTFT / prefill 往返** —— 这是今天 `workers=1` 下最贵的部分；
3. **跨页去重成为可能** —— 并行路径下模型看不到相邻页，只能靠第二个模型去猜，
   而它猜错的方式是**删掉一道题**（见 T3）。

**与 N 无关的净删除**只有两笔：润色调用（重写整篇输出）与文件名调用。

**因此 `COMBINED_VISION_MAX_IMAGES` 应由实测决定，而不是拍脑袋。** 阶段 0 之后先跑一次
对照：同一组 8 张真实题图，分别设 `=8` 和 `=4`，看 `StageTimings` 里 classify+ocr
那一段的实际秒数。若合并路径慢过并行路径 30% 以上，把默认值降到 4 即可 —— 一行配置的事。

> 上表是**调用次数**的确定结论；**耗时**是估算，须由第 8 节的实测数据回填替换。

### 0.5 明确不做的事

- **不做「分类 + 转录 + 求解」三合一**：`SOLVER_ROUTING_CONFIG` 的两个分支
  （`config.py:60-66`）**都是 `deepseek`**，迁移后题型分类对求解器路由的影响归零。
  但求解**必须先知道分类结果**才能选 prompt（`core_pipeline.py:719-741` 分
  视觉推理 / 文本两条分支）。把分类塞进求解的同一次调用，等于让模型自己决定用哪套
  prompt —— 逻辑上不成立，且会牺牲 `VISUAL_REASONING` 的专用 prompt
  （`prompts.py:200-217`）。
  **正确做法是 T1（分类与转录合并），而不是三合一。**
- **不动 `IMAGE_MAX_EDGE`**：DeepSeek 会把图缩到约 1300×1300 等效，本地调大收益有限，
  且会让「OCR 质量变化」无法归因（见 C2）。
- **不删 `zhipu` 分支**：回退成本极低，而 C5 的单点依赖风险是真实的。

---

## 1. 目标与验收标准

**目标**
把项目的全部视觉调用（分类 / 逐页 OCR / 合并调用 / 视觉推理 / 核对）从
智谱 GLM-4.6V 系列切换到 DeepSeek `deepseek-flash`，且**识别质量不倒退**。

**验收标准**

| # | 标准 | 判定方式 |
|---|---|---|
| S1 | **OCR 不倒退**：同一组题图，新旧两版转录文本在「题目要素」（题干、选项、数字、公式）上无遗漏 | `python -m tools.vision_ab check -i <图或目录>` 人工比对 + 关键要素断言 |
| S2 | **分类不倒退**：题型分类结果与旧版一致率 ≥ 90% | A/B 工具输出混淆矩阵 |
| S3 | **思考模式确实关闭**：视觉调用的响应中 `reasoning_content` 恒为空，且 `usage` 无思考 token | 单测断言 payload 含 `thinking.type=disabled`；实测日志无 reasoning |
| S4 | **不回归**：现有 pytest 全绿 | `pytest` |
| S5 | **一键回退**：`VISION_PROVIDER=zhipu` 后全部行为与当前完全一致 | 单测 + 手工跑一次完整任务 |
| S6 | **成本可见**：`accounts.COST_TABLE` 反映真实单价，额度扣减不再低估 | 单测校验单价 |
| S7 | **配置自检**：缺少对应 provider 的密钥时，启动即报明确错误 | `tools/diag.py` 输出 + 单测 |
| S8 | **多图合并生效**：8 图任务的视觉请求数 = **ceil(N / VISION_BATCH_SIZE)**（分批合并，N=8 → 2） | 日志的请求数 + `_probe/probe_e2e_real.py`；`_emit_usage` 的 `calls` 是**计费倍数**（恒 1），不用于此判定 |
| S9 | **润色按需触发**：合并路径下（转录时已在 `CONT` 页去重）不发起润色调用 | `timings.polish == 0` 且无 polish 用量事件 |
| S10 | **辅助链路思考已关**：`ask_for_analysis` 的响应中 `reasoning_content` 恒为空 | 单测断言 payload 含 `thinking.type=disabled` |
| S11 | **转录已归档**：每个任务落盘 `<OCR_DIR>/<日期>/<task_id>.md`，页数与图片数一致 | 文件存在性 + 分页数断言 |
| S12 | **无 LaTeX 静默损坏**：抽查 `\frac` / `\begin` / `\theta` 未被转义成控制字符 | 人工比对归档文本与原图 |

---

## 2. 事实基线（本方案的事实依据）

### 2.1 模型事实（官方文档，2026-09-19）

| 项 | 值 |
|---|---|
| **视觉模型名** | **`deepseek-flash`**（版本 = DeepSeek-V4.1-Flash） |
| 不支持视觉的模型 | `deepseek-v4-pro` —— 官方价格表「图像理解」列明确标注**不支持** |
| 已下线别名 | `deepseek-v4-flash`、`deepseek-v4-flash-vision-exp`（仍可调用，请求由 V4.1-Flash 承接） |
| BASE URL (OpenAI 格式) | `https://api.deepseek.com` |
| 上下文 / 输出上限 | 1M / 384K |
| 并发限制 | 2500 |

### 2.2 图片接口契约

- 格式：`{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,<B64>"}}`
  —— **与 `vision_client._build_user_content` 现有实现逐字相同**；
- 支持格式：JPEG / PNG / GIF / WebP（按**文件实际内容**判断，不看 MIME）；
- 限制：请求体 48 MiB；单图 base64 ≤32 MiB；单请求最多 **600** 张图；
  单图单边最长 8192 px（**单请求 ≥15 张图时降为 4096 px**）；
- **`detail` 字段**：`low` = 缩到 512×512；`high`/`original`/`auto` = 保留原图
  （`auto` 当前等价于 `original`）。默认不传 = `auto`；
- 图片**只能出现在 user 消息**中，system / assistant 携带图片返回 **400**。

### 2.3 图片 token 换算（C2 的来源）

> 每张图进模型前会被自动缩放：总像素 <约 544×544 的会被放大；
> 更大的会被缩小到「约相当于 1300×1300 的图片」。
> 因此每张图消耗的 token 数存在上限（**1024 个**）。

对照现状：`IMAGE_MAX_EDGE=1600`，一张 1600×1233 的截图 ≈ 1.97 M 像素，
会被 DeepSeek 再缩到 ≈1.69 M 像素（约 0.86 倍，长边约 1478 px）。
**即：本地预处理做了 16 倍压缩之后，服务端还会再缩一刀。** 这是 C2。

### 2.4 价格（元 / 百万 token，官方页）

| 模型 | 输入(缓存命中) | 输入(未命中) | 输出 |
|---|---|---|---|
| `deepseek-flash` 空闲时段 | 0.02 | **1** | **4** |
| `deepseek-flash` 高峰时段 | 0.04 | **2** | **8** |
| `deepseek-v4-pro` 空闲 | 0.15 | 4.5 | 13.5 |

> 高峰时段 = 北京时间周一至周五（不含法定节假日）9:00–12:00、14:00–18:00；
> 其余时段（含周末与节假日全天）为空闲时段，价格为高峰的一半。

**对照项目现状**（`webapp/accounts.py` 的 `COST_TABLE`）：

```python
"deepseek-flash": (0.5, 2.0),      # ← 错：实际 1–2 / 4–8，长期低估 2–4 倍
"GLM-4.6V-FlashX": (0.5, 1.5),     # ← 与官方 GLM 定价一致
"GLM-4.6V": (2.0, 6.0),
```

### 2.5 思考模式（C3 / C4 的来源）

- 开关（OpenAI 格式）：`{"thinking": {"type": "enabled" | "disabled"}}`，
  **必须放进 `extra_body`**（OpenAI SDK 不认这个顶层字段）；
- **默认开启，且 effort 默认 high**；
- 思考强度：`reasoning_effort="low"|"high"|"max"`（项目 `SOLVER_REASONING_EFFORT` 已在用）；
- 思考内容经 `reasoning_content` 返回，与 `content` 同级；
- **思考模式不支持 `temperature` / `presence_penalty` / `frequency_penalty`**
  （传了不报错，但静默不生效）；
- **`top_p` 仅在思考模式下生效，有效范围 0.95–1.0**（低于 0.95 按 0.95 处理）；
  **非思考模式下 `top_p` 恒为 1.0，传入被忽略**。

`solver_client._build_payload` 已经踩过并解决了这个坑（见其注释：
「关闭思考必须**显式**下发 `thinking.type=disabled`。实测只把 `extra_body`
整个省略时（旧行为）模型照样思考」）。**`vision_client` 当前完全没有这段逻辑 —— 这是迁移最大的坑。**

### 2.6 现有耦合点清单（2026-09-19 全仓 grep 结果）

| 位置 | 内容 | 迁移动作 |
|---|---|---|
| `problem_solver_agent/config.py:45-51` | `VISION_BASE_URL` / `VISION_CLASSIFY_MODEL` / `VISION_REASONING_MODEL` / `VISION_PROVIDER_NAME` | **改写**为 provider 表 |
| `problem_solver_agent/vision_client.py:41-62` | `_get_vision_client()` 读死 `ZHIPU_API_KEY` + `VISION_BASE_URL` | **改写**为按 provider 选 |
| `problem_solver_agent/vision_client.py:125-127` | payload 无 `thinking` 参数 | **新增** `extra_body` |
| `problem_solver_agent/vision_client.py:399-405` | `top_p=0.8, temperature=0.7` | 改 provider 分支 |
| `problem_solver_agent/verify.py:146` | `model or config.VISION_REASONING_MODEL` | 自动跟随，**无需改** |
| `problem_solver_agent/pipeline.py:134` | 校验 `ZHIPU_API_KEY` | 改为校验 `VISION_API_KEY` |
| `problem_solver_agent/main.py:27` | 校验 `ZHIPU_API_KEY` | 同上 |
| `webapp/app.py:36` | 启动自检 `ZHIPU_API_KEY` | 同上 |
| `webapp/routes.py:1058` | `vision_configured = bool(ZHIPU_API_KEY)` | 同上 |
| `tools/diag.py:82` | 诊断输出 | 同上 |
| `webapp/accounts.py:40-47` | `COST_TABLE` 单价错误 | **修正** |
| `webapp/usage.py:27,102` | `TOKENS_PER_IMAGE=700`；写死 `GLM-4.6V-FlashX` | **修正** |
| `.env.example` / `README.md` / `docs/*` | 文案 | 更新 |
| `start_web.bat:32,40` | 依赖检查与提示 | 更新 |
| `tests/test_accounts.py:250` / `test_verify.py:94` / `test_vision_client.py:118` | 硬编码 GLM 模型名 | 更新 |

**本轮（多图提速复核）新发现的耦合点**：

| 位置 | 内容 | 迁移动作 |
|---|---|---|
| `problem_solver_agent/config.py:196` | `OCR_PARALLEL_WORKERS=1`，逐页 OCR **串行** | 提到 4（T2；落地值） |
| `problem_solver_agent/config.py:134-135` | `USE_COMBINED_VISION_CALL="auto"` + `COMBINED_VISION_MAX_IMAGES=1`，多图**连请求都不发** | 上限提到 8（T1） |
| `problem_solver_agent/prompts.py:44-64` | 合并调用用 JSON 协议，LaTeX 反斜杠需转义 | 改为分隔符协议 + 新增 `LAYOUT`（T1/T3） |
| `problem_solver_agent/vision_client.py:291-327, 333-366` | 合并调用闸门与 JSON 解析 | 新增分隔符解析 + 三级回退链 |
| `problem_solver_agent/solver_client.py:359-389` | `ask_for_analysis` **未关思考**，`temperature=0.7` 静默失效 | 补 `extra_body`（T5） |
| `problem_solver_agent/core_pipeline.py:787-805` | `_generate_filename` 每次任务一次 deepseek 调用，**不计时不计费** | 本地生成 + 补进 `StageTimings`/`UsageReport`（T4） |
| `problem_solver_agent/core_pipeline.py:476-487` | `_cached_or_compute` **只包装合并路径**，并行路径不缓存 | 并行路径也缓存 |
| `problem_solver_agent/pipeline.py:23-42` | `reclassify_problem_type` 是**死代码**（全仓无调用点、无测试覆盖） | 删除函数，但**先把 `ML_CODING` 标签上移**到分类 prompt（见下） |
| `webapp/models.py:9-17` | `_TASK_COLUMNS` 幂等迁移字典 | 加 `problem_text` / `ocr_raw_text` / `vision_mode` 三列 |
| `webapp/models.py:108-118` | `update_task` 的 `allowed` 字段白名单 | **必须同步**加上述三键，否则抛「未知的任务字段」 |

**`ML_CODING` 已经是一条不可达的路径**（这条比删死代码本身更重要）：

`reclassify_problem_type` 是 `ML_CODING` 的**唯一产生者**，而它从未被调用。
同时 `CLASSIFICATION_PROMPT`（`prompts.py:19-22`）与 `classify_and_transcribe` 的
`valid_types`（`vision_client.py:357-360`）**都不含这个标签**。于是以下全成了摆设：
`map_final_type` 的 `ML_CODING` 分支、`determine_solver` / `build_prompt` 的分支、
`prompts.PROMPT_TEMPLATES["ML_CODING"]`（`prompts.py:261-293`，一份很认真的
面试讲解模板）、`core_pipeline._LONG_ANSWER_TYPES` 里的 `"ML_CODING"`、
前端 `frontend/src/lib/problems.ts:10` 的展示映射。

**做法**：把 `ML_CODING` 加进分类 prompt 的标签表和两处 `valid_types`，让视觉模型直接判
—— 它读得到题面全文，比关键词匹配（`"numpy"` `"torch"` `"cnn"`…）准得多。然后才删除
`reclassify_problem_type` 与 `ML_KEYWORDS` / `CODING_KEYWORDS`。
**只删不复原是不划算的** —— 删掉一个已经写好的能力，比多一个标签的误判风险更亏。

**前端无需改动**：`frontend/src` 只有 `output/AnswerCard.test.tsx` 里出现 `GLM-4.6V`
（测试夹具），生产代码全部从 API 取模型名。

**注意（迁移陷阱）**：`webapp/models.py` 的 `stage_cache` 按 `(task_id, stage)` 缓存，
**不含模型名**。切换 provider 后，**已存在的任务**若走「重试」，会直接命中旧
provider 写下的转录缓存。

**解法见组 H**：把模型名写进缓存 payload 并在读取时比对（读取方 `_cached_or_compute`
本来就要解 payload，加一次字符串比较即可），provider 切换后旧缓存**自动失效**，
不需要改表结构、也不需要人工清理存量任务。若想更彻底，也可以在迁移时跑一次
存量任务的 `clear_stage_cache`（`models.py:231-233`）。

---

## 3. 代码改动（按子系统分组）

### 组 A：配置 provider 化 —— `problem_solver_agent/config.py`

把「一组写死的视觉常量」升级为「一张 provider 表 + 派生常量」，**保持现有一切
调用点不变**（`VISION_CLASSIFY_MODEL` 等名字继续存在）。

```python
# --- 2. 视觉模型配置（provider 化）---
# 视觉层用哪家：deepseek（默认）/ zhipu。
# 想一键回退到智谱，只需在 .env 里设 VISION_PROVIDER=zhipu，代码不用动。
VISION_PROVIDER = os.getenv("VISION_PROVIDER", "deepseek").strip().lower()

VISION_PROVIDER_CONFIG: dict[str, dict[str, str]] = {
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url":    "https://api.deepseek.com",   # 官方 OpenAI 兼容端点
        "classify_model":  "deepseek-flash",         # 分类 + OCR
        "reasoning_model": "deepseek-flash",         # 视觉推理 + 核对
        "supports_thinking_control": "1",            # 特殊能力开关，见组 B
    },
    "zhipu": {
        "api_key_env": "ZHIPU_API_KEY",
        "base_url":    "https://open.bigmodel.cn/api/paas/v4/",
        "classify_model":  "GLM-4.6V-FlashX",
        "reasoning_model": "GLM-4.6V",
        "supports_thinking_control": "0",
    },
}

if VISION_PROVIDER not in VISION_PROVIDER_CONFIG:
    VISION_PROVIDER = "deepseek"          # 未知值一律回落默认，不抛异常

_VISION_CFG = VISION_PROVIDER_CONFIG[VISION_PROVIDER]

VISION_BASE_URL         = _VISION_CFG["base_url"]
VISION_CLASSIFY_MODEL   = _VISION_CFG["classify_model"]
VISION_REASONING_MODEL  = _VISION_CFG["reasoning_model"]
VISION_PROVIDER_NAME    = VISION_PROVIDER

# 视觉层密钥统一出口：所有校验点（main/pipeline/app/routes/diag）改用它，
# 这样切换 provider 时校验逻辑不用跟着改。
def _vision_api_key() -> str | None:
    return os.getenv(_VISION_CFG["api_key_env"])

VISION_API_KEY = _vision_api_key()
```

同时新增两个可调项（**默认值保持当前行为**）：

```python
# 是否关闭视觉调用（分类/OCR/推理/核对）的思考模式。
# DeepSeek 思考模式默认开启且 effort=high；不关掉会吃光 max_tokens 导致正文为空。
# 详见 docs/plans/deepseek_vision_migration.md 的 C3。
VISION_DISABLE_THINKING = os.getenv("VISION_DISABLE_THINKING", "true").lower() in ("true", "1", "yes")
# 视觉调用的输出上限。DeepSeek 上限 384K；从 8192 提高可容纳更长转录，
# 但仍建议先用 8192 跑 A/B，避免把成本一次性抬高。
# 视觉调用的输出上限。DeepSeek 上限 384K；从 8192 提高可容纳更长转录。
# **落地值是 32768**（本节的 8192 只是"先用小值跑 A/B"的过渡建议，见组 H）：
# 8 页转录约 6–16K token，8192 会把合并调用截断，而协议换掉之后这才是唯一的硬约束。
VISION_MAX_TOKENS = int(os.getenv("VISION_MAX_TOKENS", "32768"))
```

> **为什么不直接复用 `solver_client.get_client("deepseek")`**：两者超时策略不同
> （求解 `API_TIMEOUT=600s` vs 视觉 `VISION_TIMEOUT=120s`），共用会改变视觉的
> 超时行为。共用 **key / base_url 常量**即可，client 实例各建各的。

### 组 B：视觉客户端 —— `problem_solver_agent/vision_client.py`

**B1. 客户端按 provider 初始化**（替换 `_get_vision_client`）：

```python
_vision_clients: dict[str, OpenAI] = {}   # provider -> client

def _get_vision_client(provider: str | None = None) -> OpenAI | None:
```
- 密钥从 `config.VISION_PROVIDER_CONFIG[provider]["api_key_env"]` 对应环境变量取；
- 缺失时日志指明**具体是哪个环境变量**（现有文案写死 `ZHIPU_API_KEY`，必须改）；
- `timeout=config.VISION_TIMEOUT`、`max_retries=0` 保持不变。

**B2. payload 增加思考控制（最关键的一处）**：

```python
payload: VisionCompletionPayload = {
    "model": model_name, "messages": messages,
    "max_tokens": config.VISION_MAX_TOKENS, "stream": stream,
}
# DeepSeek 思考模式默认开启，且 effort 默认 high。分类/OCR 这类短任务
# 一旦开思考，思考过程会把 max_tokens 吃光、正文为空 —— 与 solver 踩过的
# 是同一个坑（见 config.py 的「思考模式：首选与按需升级」）。
if config.VISION_PROVIDER_NAME == "deepseek" and config.VISION_DISABLE_THINKING:
    payload["extra_body"] = {"thinking": {"type": "disabled"}}
```

**B3. 视觉推理参数按 provider 分支**（`solve_visual_reasoning_problem`）：

```python
extra = (
    # DeepSeek：非思考模式下 temperature/top_p 均不生效，直接不传，
    # 避免"以为设了 0.7 其实没用"的错觉。
    {"extra_body": {"thinking": {"type": "disabled"}}}
    if config.VISION_PROVIDER_NAME == "deepseek"
    else {"top_p": 0.8, "temperature": 0.7}   # 智谱保持原样
)
```

**B4. 保持 `max_tokens: 8192` 的截断告警**（现有 `finish_reason == "length"` 日志）
—— 迁移后这是判断「转录被截断」的唯一信号，务必保留。

### 组 C：修正计费 —— `webapp/accounts.py` + `webapp/usage.py`

```python
COST_TABLE: dict[str, tuple[float, float]] = {
    # DeepSeek：按**高峰时段**价格计（宁可高估，额度系统的目的是限制滥用）。
    # 官方单价：空闲 1/4，高峰 2/8；缓存命中仅 0.02–0.04。
    "deepseek-flash": (2.0, 8.0),
    "deepseek-v4-pro": (9.0, 27.0),
    "GLM-4.6V-FlashX": (0.5, 1.5),   # 仅 VISION_PROVIDER=zhipu 时用到，保留
    "GLM-4.6V": (2.0, 6.0),
    "default": (2.0, 8.0),
}
```

`webapp/usage.py`：
- `TOKENS_PER_IMAGE: 700 → 1024`（DeepSeek 官方给出的每图 token **上限**，保守取值）；
- `estimate_task_cost` 里写死的 `"GLM-4.6V-FlashX"` 改为读取视觉层当前模型：
  ```python
  from problem_solver_agent import config as core_config
  estimate_cost(core_config.VISION_CLASSIFY_MODEL, vision_input, 0)
  ```

### 组 D：密钥校验链统一

把以下各处从「检查 `ZHIPU_API_KEY`」改为「检查 `config.VISION_API_KEY`」，
并让错误信息带上当前 provider 与所需环境变量名：

| 文件:行 | 改法 |
|---|---|
| `problem_solver_agent/pipeline.py:134` | 校验 `config.VISION_API_KEY`；文案含 `VISION_PROVIDER` 与 `api_key_env` |
| `problem_solver_agent/main.py:27` | 同上（日志文案同步改） |
| `webapp/app.py:36` | 同上（`problems.append` 文案） |
| `webapp/routes.py:1058` | `"vision_configured": bool(core_config.VISION_API_KEY)` |
| `tools/diag.py:82` | 输出当前 `VISION_PROVIDER` + 对应密钥是否已配置 |

### 组 E：配置自检与 CLI

- `tools/diag.py`：新增一行输出「视觉层：provider=deepseek，模型=deepseek-flash，
  思考=关闭，密钥=已配置」；
- **新增 `tools/vision_ab.py`**（见组 F），作为 S1/S2 的判定工具；
- `.env.example`：
  ```env
  # 视觉层供应商：deepseek（默认）/ zhipu
  # 设为 zhipu 可一键回退到 GLM-4.6V 系列
  VISION_PROVIDER=deepseek
  # DeepSeek 密钥：视觉 + 求解共用（VISION_PROVIDER=deepseek 时必填）
  DEEPSEEK_API_KEY=
  # 智谱密钥：仅在 VISION_PROVIDER=zhipu 时需要
  # ZHIPU_API_KEY=
  ```
- `start_web.bat`：依赖检查从「必须有 ZHIPU_API_KEY」改为按 provider 判定。

### 组 F：新增 A/B 评测工具 `tools/vision_ab.py`

**这是本方案的核心闸门**——没有它，C1/C2 的代价无法量化，「换不换」只能靠信仰。
设计复用 `watermark_removal.md` 里 `eval` 子命令的思路：

```powershell
# 用同一组题图，分别走两个 provider 的 OCR，输出差异报告
python -m tools.vision_ab check -i <图或目录>

# 只跑某一个 provider
python -m tools.vision_ab check -i <目录> --provider deepseek
```

输出（写入 `_probe/vision_ab/<时间戳>/report.md`）：

| 指标 | 说明 |
|---|---|
| 逐页转录全文（两版并排） | 人工比对用 |
| 字符数差 / 数字与公式要素差 | 自动断言：题干数字、选项、公式片段不应丢失 |
| 题型分类一致率 | 混淆矩阵 |
| 耗时 / 估算费用 | 直接对应 C1 |
| 被 `max_tokens` 截断的页数 | `finish_reason == "length"` 计数 |

实现要点：
- **不改动生产代码路径**：通过 `VISION_PROVIDER` 环境变量在子进程里跑两遍，
  或直接把 `provider` 参数透传进 `vision_client`（组 B 已让 `_get_vision_client(provider=...)` 支持）；
- 结果 JSON 落盘，便于回归对比。

### 组 G：测试与文档

**新增 `tests/test_vision_provider.py`**

1. `VISION_PROVIDER=deepseek` 时，构造的 payload 含
   `extra_body == {"thinking": {"type": "disabled"}}`（S3，**回归保护**）；
2. `VISION_PROVIDER=zhipu` 时 payload **不含** `extra_body`，且
   `top_p`/`temperature` 仍在（S5）；
3. `VISION_PROVIDER=zhipu` + `VISION_CLASSIFY_MODEL` == `"GLM-4.6V-FlashX"`
   （证明回退后与现状逐字节一致）；
4. 未知 `VISION_PROVIDER` 值 → 回落 `deepseek`，不抛异常；
5. 密钥缺失时 `_get_vision_client()` 返回 `None` 且日志含**正确的环境变量名**。

**更新既有用例**

| 文件 | 改法 |
|---|---|
| `tests/test_vision_client.py:118` | 模型名参数化（不必硬编码 GLM） |
| `tests/test_verify.py:94` | 同上；或显式传 `model=` 保持用例独立于 provider |
| `tests/test_accounts.py:250` | COST_TABLE 修正后更新断言；新增 `deepseek-flash` 单价断言（S6） |
| `tests/test_core_pipeline.py` | 补一条「provider=zhipu 时全流程与基线一致」的回退用例 |

**文档**：`README.md`（第 44 行密钥表、第 234-235 行模型示例）、
`docs/USER_GUIDE.md`（第 45/48/243/262/352/422/593 行）、
`docs/DEPLOY.md`（第 59/351 行）、`docs/API.md`（第 240/857-867 行的价目表）、
`.claude/CLAUDE.md`（「Provider 系统」章节）。

### 组 H：多图提速（设计详见 0.4 节）

| 文件:行 | 改动 |
|---|---|
| `config.py:134-135` | `COMBINED_VISION_MAX_IMAGES` 1 → **8**；`USE_COMBINED_VISION_CALL` 保持 `auto`（语义变为「≤8 张就合并」） |
| `config.py:196` | `OCR_PARALLEL_WORKERS` 1 → **4**（只影响回退路径）。顺带补上 `os.getenv` 覆盖 —— 它现在是裸常量 |
| `config.py:45-51` | 新增 `VISION_MAX_TOKENS`(32768) / `VISION_COMBINED_TIMEOUT`(300) / `VISION_INLINE_MERGE` / `FILENAME_MODE` / `AUX_TIMEOUT`(300) |
| `prompts.py:44-64` | 重写 `CLASSIFY_AND_TRANSCRIBE_PROMPT` 为 `<<<PAGE n\|NEW/CONT>>>` 协议；**保留**原 JSON 版作为第二顺位回退 |
| `prompts.py:71-101` | `TEXT_MERGE_AND_POLISH_PROMPT` 增加「场景判断」段（多题时禁止跨片段删除，见 T3） |
| `vision_client.py:126` | `max_tokens` 从**写死的 8192** 改为 `config.VISION_MAX_TOKENS` —— 不改这里，384K 输出上限根本用不上 |
| `vision_client.py:259-288` | 新增 `parse_page_protocol()` / `refill_pages()` / `_collect_stream()` |
| `vision_client.py:310-366` | `classify_and_transcribe()` 改为三级回退链，**部分成功即采用**（不再整批返回 `None`） |
| `vision_client.py:132-172` | 流式下需在收集器里**补一层重试**（现有循环只覆盖 `create()` 抛出的异常） |
| `solver_client.py:359-389` | `ask_for_analysis` 补 `extra_body={"thinking": {"type": "disabled"}}`（T5） |
| `solver_client.py:371` | 硬编码 `timeout=120.0` 改为 `config.AUX_TIMEOUT`（T5） |
| `core_pipeline.py:787-805` | `_generate_filename` 双模式：优先用求解正文首行的 `FILE:` 建议，其次 `utils.extract_question_numbers` 本地生成，最后才调模型；耗时与用量补进 `StageTimings`/`UsageReport`（T4） |
| `core_pipeline.py:526-574` | `_run_solve_attempt` 的流式循环剥掉首行 `FILE:` 建议（取消检查、`_should_escalate`、答案卡抽取都不受影响） |
| `core_pipeline.py:196-241` | 抽出 `_transcribe()`：合并/并行两条路径**统一包进一次 `_cached_or_compute`** |
| `core_pipeline.py:489-524` | `_textualize` 增加内联分支：`vision_mode == "combined"` 且无失败页 → `join_by_continuation()` 本地拼接、跳过模型调用 |
| `problem_solver_agent/pipeline.py:23-42` | 删除死代码 `reclassify_problem_type`（连带 `ML_KEYWORDS` / `CODING_KEYWORDS`） |

> **`_cached_or_compute` 现状比想象中糟**：它只包了 `classify_and_transcribe` 一个调用，
> 而这个调用在 `auto` + 上限 1 的配置下对多图**直接返回 `None`**。结果就是
> **缓存几乎永远写不进东西，而实际走的并行路径 100% 不缓存** —— 一次 8 图任务在求解阶段
> 失败后点「重试」，会重新付 9 次视觉调用的钱。这是投入产出比最高的一处修复。
>
> 同时把 **模型名存进缓存 payload 并在读取时比对** —— provider 切换后旧缓存自动失效，
> 一举解决本文档 2.6 节末尾提到的「迁移后命中旧 provider 转录缓存」的坑，
> 且不需要改 `stage_cache` 的表结构。

> **测试影响**：`tests/test_vision_client.py:28-67` 的合并调用用例都**显式
> monkeypatch** 了 `USE_COMBINED_VISION_CALL` / `COMBINED_VISION_MAX_IMAGES`，
> 因此改默认值**不会**破坏它们。但用例 `test_multi_image_group_skips_the_combined_request`
> 的语义是「多图应当跳过合并」，需按新默认值改写为「多图应当合并」，
> 并新增分隔符协议的解析用例（含 LaTeX 反斜杠、页数不匹配、缺 `LAYOUT` 字段）。

### 组 I：转录双层落盘

现状覆盖度：

| 用途 | 现状 | 缺口 |
|---|---|---|
| 解答文件里带上题目 | **已有**：`# 题目文本` 小节（`_write_header`，`core_pipeline.py:779-782`） | `VISUAL_REASONING` 任务（`transcribed_text == "N/A"`）不写 |
| 归档 OCR 原始结果 | **没有**：`pages` 列表仅在内存，并行路径用完即丢 | 需新增 |
| 历史可搜索可复读 | 部分：`stage_cache` 存了 `{"problem_type","pages"}`，但**仅合并路径**写入 | 需补并行路径 |

**设计**（两层都是**零模型成本** —— 文本已经在内存里）：

```
<OCR_DIR>/<YYYY-MM-DD>/<task_id>.md   # 第 1 层：原始逐页 OCR（新增）
{解答文件}.md                          # 第 2 层：求解用的最终文本（维持现状）
```

`config.py` 新增 `OCR_DIR = ROOT_DIR / "ocr"`，与 `SOLUTION_DIR` / `PROCESSED_DIR`
同级。**用独立目录而不是塞进 `webapp/solutions`** 有三个理由：

1. Web 与 CLI 自动共用同一份，不需要像解答文件那样再 `_sync_to_root_solutions()` 复制一遍；
2. 能直接被手机端 Samba 看到；
3. **关键**：**绝不能把原始 OCR 塞进解答文件的 `# 题目文本` 小节** ——
   `webapp/pipeline.extract_problem_text`（`:227-249`）和前端
   `solutionText.ts` 的 `stripPreamble` 都是「从 `# 题目文本` 切到下一个 `---`」，
   塞进去会被当成题目文本喂给换路重解，还会让答案卡抽取错位。

**用 `task_id` 而不是最终文件名做 stem**：转录在第 1 阶段就产生了，而文件名在第 5 阶段
才由求解器给出；用 `task_id` 保证**中途取消/求解失败也留下了归档**，且与
`webapp/uploads/<task_id>/` 天然可配对。

归档格式：

```markdown
---
task_id: 20260919-a1b2c3
created: 2026-09-19 22:10:03
vision_provider: deepseek
vision_model: deepseek-flash
vision_mode: batched          # combined | batched | json | parallel
images: [IMG_220101.jpg, IMG_220104.jpg]
pages: 8
failed_pages: []
---

## 第 1 页 — IMG_220101.jpg — NEW

（原始转录逐字）

## 第 2 页 — IMG_220104.jpg — CONT

（原始转录逐字；CONT 页已按模型判断省略与上一页重复的内容）

## 第 3 页 — IMG_220106.jpg — 识别失败

> 该页 OCR 返回空内容；该页已由 refill_pages 补做 / 或求解阶段回退为原图直读。
```

**写入时机是视觉阶段结束后立刻写**（`run()` 的 `:240` 附近），而不是成功收尾时写 ——
这样取消与求解失败也保住了 OCR 归档。

**顺带补一个真实缺口**：`_write_header`（`core_pipeline.py:753-785`）的 frontmatter 里
加两行 `task_id:` 与 `ocr_archive:`。目前解答文件名不含 `task_id`，导致
「解答文件 ↔ tasks 表 ↔ uploads 目录」三者无法互相对照。前端
`solutionText.ts` 的 `FRONTMATTER_KEYS` 白名单只要块内出现 `problem_type:` 就会匹配，
加新键不影响剥离逻辑。

**历史搜索**：光有文件不够 —— 搜索需要按用户/时间过滤，文件 grep 做不到。
给 `tasks` 表加三列，走既有幂等迁移（`webapp/models.py:9-17` 的 `_TASK_COLUMNS`
加行，`_migrate_tasks:74-83` 自动补列）：

```python
"problem_text": "TEXT DEFAULT ''",   # 送入求解的题目文本（润色后）
"ocr_raw_text": "TEXT DEFAULT ''",   # 逐页原始 OCR 拼接（便于命中被润色改写的关键词）
"vision_mode":  "TEXT DEFAULT ''",   # combined | batched | json | parallel，用于统计各转录路径的耗时
```

> **两个容易漏的点**：
> ① `update_task` 的 `allowed` 集合（`models.py:108-118`）**必须同步加这三个键**，
> 否则会抛「未知的任务字段」；
> ② **不要一上来就上 FTS5** —— `TASK_RETENTION_COUNT=100`，`LIKE '%kw%'`
> 在百行表上是微秒级；而 FTS5 默认分词器对中文无效，必须 `tokenize='trigram'`，
> 还多一张虚表和一套同步逻辑，收益为零。留一句注释说明「表涨到万级时再换」即可。

---

## 4. 数据流（迁移后）

```
截图文件 ──> vision_client._build_user_content
              └─> image_prep.prepare_for_api         (EXIF 校正 → 缩放 1600 → JPEG q80)
                    └─> data_uri "data:image/jpeg;base64,..."
                          └─> [组 B] extra_body={"thinking":{"type":"disabled"}}
                                └─> POST https://api.deepseek.com/chat/completions
                                      model="deepseek-flash"
                                      └─> 服务端二次缩放（≈1300×1300 等效，≤1024 tok/图）
                                            └─> 转录文本 / 题型
                                                  └─> solver_client (同一个模型 deepseek-flash)
                                                        └─> 流式求解（thinking 按现有两段式策略）
```

**关键观察**：迁移后**视觉与求解是同一个模型**。这带来 prompt 风格与错误处理的统一，
但真实代价是 **C5 单点依赖**（一个 provider 挂了全流程挂）与 **C6 成本归因**，
**不是并发** —— 项目 `MAX_CONCURRENT_TASKS=2`、OCR 串行，对比 DeepSeek 的 2500
并发上限差三个数量级，并发从来不是约束。

**8 图任务的完整数据流（T1/T5 生效后）**：

```
截图 ×8 ──> vision_client._build_user_content
              └─> image_prep.prepare_for_api      (EXIF 校正 → 缩放 1600 → JPEG q80)
                    └─> 8 个 data_uri
                          └─> [组 B] extra_body={"thinking":{"type":"disabled"}}
                                └─> POST https://api.deepseek.com/chat/completions
                                      model="deepseek-flash"  ← 一次请求带 8 张图
                                      └─> 服务端二次缩放（每图 ≈1300×1300 等效，≤1024 tok）
                                            └─> PAGE 协议：<<<TYPE>>>标签 + 8 个
                                                <<<PAGE n|NEW/CONT>>> 页块 + <<<END>>>
                                                  ├─> [组 I] <OCR_DIR>/<日期>/<task_id>.md 落盘
                                                  │        （页数 = 图片数；缺页标"识别失败"）
                                                  ├─> 缺页 → refill_pages 单页补做（补 k 页 = k 次调用）
                                                  ├─> 解析失败 → JSON 协议 → 分类 + 并行 OCR。
                                                  │    **没有 LAYOUT 字段** —— 版面判断就是每个页块上的
                                                  │    NEW/CONT 标记，它支持"前 3 页同一题、后 5 页独立题"
                                                  │    的混合场景，单一字段做不到
                                                  ├─> VISION_INLINE_MERGE=true → join_by_continuation()
                                                  │   本地拼接（NEW 用空行、CONT 用单换行），跳过润色
                                                  └─> solver_client.stream_solve
                                                        （thinking 按现有两段式策略）
                                                        └─> 答案卡 + 命名归档
```

> **`{task_id}_pages.json` 是设计稿的早期形态，落地为 `.md` 归档**：`.md` 直接给人看
> （手机端 Samba 可读），frontmatter 里已带 `vision_mode` / `failed_pages` 等结构化字段，
> 再存一份 JSON 只是重复。同理 `LAYOUT` 字段被 NEW/CONT 标记取代。

---

## 5. 边界情况与失败模式

| 情况 | 处理 |
|---|---|
| **思考模式没关掉** | 视觉 payload 强制带 `thinking.type=disabled`；单测断言（S3） |
| **转录被 `max_tokens` 截断** | 保留 `finish_reason == "length"` 告警；A/B 报告统计截断页数；必要时上调 `VISION_MAX_TOKENS` |
| **小字/公式 OCR 退化（C2）** | A/B 必须先跑；若退化，把 `IMAGE_MAX_EDGE` 提到 2048 重测 —— 注意这**不是**万能解，服务端仍会缩到 1300 等效，实际增益有限 |
| **`top_p`/`temperature` 静默失效** | 组 B3：DeepSeek 分支不传这俩参数，避免「设了但没用」的错觉 |
| **单请求图片数超限** | DeepSeek 上限 600 张；项目分组窗口通常个位数，无风险。仍建议在 `_call_vision_api` 加一句断言级别的日志 |
| **单图超 32 MiB** | 预处理已压到 ~187 KB，无风险 |
| **system/assistant 消息带图 → 400** | `_build_user_content` 只产出 user 消息，无风险 |
| **切换后旧任务命中旧 provider 的 stage_cache** | 迁移时清理存量任务的 `stage_cache`（`webapp/models.py:clear_stage_cache`），或在缓存键里加 provider |
| **`ZHIPU_API_KEY` 残留但已不用** | 保留 `.env` 里的项不报错；`.env.example` 里注释掉 |
| **某个 provider 返回 4xx 且不可重试** | `_is_retryable` 现有逻辑已覆盖（429/5xx 可重试），不变 |
| **成本估算偏差** | COST_TABLE 按高峰价计（保守）；`estimate_task_cost` 跟随 `VISION_CLASSIFY_MODEL` |
| **PAGE 协议解析失败** | 三级回退：PAGE 协议 → JSON → 并行路径；每次回退都在日志里记明原因，便于统计各协议的实际成功率 |
| **8 页转录中某一页失败** | **不再整批作废**：按页定位失败下标，走 `refill_pages` 单页补做（补 k 页 = k 次调用）。只有 `<<<TYPE>>>` 也缺失时才补一次分类 |
| **模型声明的页码跳号 / 重排** | 按声明的 `n` 定位而非出现顺序 —— 跳号能被直接发现并记入 `failed_pages`，不会静默错位 |
| **`NEW`/`CONT` 标记缺失或非法** | 保守当作 `NEW`（不做去重、不做跨页合并），**保留**润色 —— 宁可多花一次调用，不可丢掉一道题的开头 |
| **模型在 CONT 页多删了内容** | `VISION_INLINE_MERGE=false` 一键回退到「合并调用 + 独立润色」；因为原始 OCR 已按页归档，可直接比对定位 |
| **LaTeX 静默损坏**（`\frac`→`␌rac`） | PAGE 协议下不再经过 JSON 转义层，此类损坏从根上消除；验收时**必须抽查 `\frac` / `\begin` / `\theta`** |
| **流式迭代中途断网** | 现有重试循环不覆盖迭代期异常，须在 `_collect_stream()` 内补一层重试；仍失败则回退并行路径 |
| **合并调用撞超时** | `VISION_COMBINED_TIMEOUT`（默认 300 s）须独立于逐页 OCR 的 `VISION_TIMEOUT`（120 s）—— 两者输出量差一个数量级，不能共用一个值 |
| **并行度提高抢占本机 CPU** | `OCR_PARALLEL_WORKERS=4` 的约束不是 API 并发（2500 远够），而是 `image_prep` 的 JPEG 解码/缩放。好在分类调用已预热内存缓存，OCR 阶段基本不再重编码 |
| **并行度提高触发 429** | `_is_retryable` 已覆盖 429 并指数退避（`vision_client.py:65-72`）；DeepSeek 并发上限 2500，本项目远未触及 |
| **迁移后 stage_cache 命中旧 provider 的转录** | 缓存键是 `(task_id, stage)`，不含 provider。迁移时清理存量任务的 `stage_cache`，或把 provider 并入缓存键 |

---

## 6. 实施顺序

> 顺序刻意设计为「**先能测，再能切，最后才清理**」。任何一步失败都能停在这。

1. **组 F（A/B 工具）先做** —— 它只是读取，不改生产路径；
2. **T5（`ask_for_analysis` 关思考 + 超时可配）** —— 独立于迁移、改动小、立刻见效，
   同时为第 3 步的"关闭思考"提供一次实证；
3. **底座（组 A/B）** —— provider 化 + 关思考 + `VISION_MAX_TOKENS` + 流式收集器。
   此时 `VISION_PROVIDER=zhipu` 应仍与现状**逐字节一致**，用一条单测锁住；
4. **跑 A/B**：`python -m tools.vision_ab check -i <真实题图目录>`，
   把结果写进本文档第 8 节；**若 S1/S2 不达标，到此为止，不继续**；
   → **2026-09-21 已补跑并通过**（4 组 32 张真实题图的要素比对 + 36 个逐图分类样本，见 §8.6）。
   注意实际命令要写成 `--provider deepseek --reference zhipu`（工具会补跑基准那一家）；
5. **切默认值**：`VISION_PROVIDER` 默认值由 `zhipu` 改为 `deepseek`；
   → **已于 2026-09-21 完成**（`config.DEFAULT_VISION_PROVIDER = "deepseek"`，闸门条件见 §8.6）；
6. **协议（PAGE 标记）** —— `parse_page_protocol` + `refill_pages` + 单测。这一步
   **不改任何调用时序**，纯粹把解析器换成可救的。**先单测覆盖再动别的**：
   LaTeX 反斜杠、截断、缺页、跳号、无 `<<<END>>>`、有客套话前缀。
   **顺序不能与第 7 步对调** —— 先开闸门后换协议，会重演 2026-09-13 那次「12 次成功 1 次」；
7. **打开闸门** —— `COMBINED_VISION_MAX_IMAGES=8`；同时把 `_transcribe()` 的两条路径
   统一进缓存、`OCR_PARALLEL_WORKERS=4`。**跑一次真实 8 图对照**，据此定
   `=8` 还是 `=4`（见 0.4 节末的判据）；
8. **内联润色** —— `VISION_INLINE_MERGE` + 润色 prompt 的「场景判断」段；
9. **OCR 归档** —— `OCR_DIR` + `_write_ocr_archive` + frontmatter 的
   `task_id`/`ocr_archive`；
10. **文件名内联** —— `FILENAME_MODE` + `_run_solve_attempt` 剥首行；
11. **落库与搜索** —— `tasks` 三列 + `update_task` 白名单 + `search_tasks`；
12. **组 C/D/E**：计费修正、校验链统一、`diag`/`bat`/`.env.example`；
13. **组 G**：测试与文档；
14. **清理**：`ML_CODING` 标签上移 → 删 `reclassify_problem_type` 与两个 KEYWORDS；
    评估是否把 `zhipu` 分支保留为长期回退选项（**建议保留** —— 成本极低，
    而 C5 的单点依赖风险是真实的）。

---

## 7. 验证方式（可复现）

```powershell
# 0) 准备：确认密钥
#    .env 里 DEEPSEEK_API_KEY 已配置（VISION_PROVIDER=deepseek 时）

# 1) 配置自检
python -m tools.diag
#    期望看到：视觉层 provider=deepseek 模型=deepseek-flash 思考=关闭 密钥=已配置

# 2) ★ A/B：同一组图，两个 provider 各跑一遍 OCR
python -m tools.vision_ab check -i "D:\Users\WZW\Pictures\Screenshots\test_images"
#    产出 _probe/vision_ab/<ts>/report.md —— 这是 S1/S2 的唯一判据

# 3) 回退验证：切成智谱，确认行为与迁移前一致
$env:VISION_PROVIDER="zhipu"; pytest tests/test_vision_provider.py -v

# 4) 回归
pytest
cd frontend; npm test

# 5) 真实 API 的端到端（生产路径：视觉 → OCR 归档 → 文本 → 求解 → 命名）
py -3.10 _probe/probe_e2e_real.py _probe/ab_images --no-archive
#    期望 16/16 通过（含 S8/S9/S11/S12 与组 I 落库字段）

# 6) 转录形态的耗时对照（**交替轮次**，不能用成块测量，理由见 §8.7）
py -3.10 _probe/probe_batch_interleaved.py _probe/ab_images --cycles 4

# 7) 两条路径的**真实 token 成本**（读服务端 usage，不是估算）
py -3.10 _probe/probe_vision_cost.py _probe/ab_images

# 8) S1 重算：对已保存的 A/B 产物重新判分（不花 API 费用）
py -3.10 _probe/rescore_s1.py

# 9) 计费口径回归（图片 token 不随请求次数放大）
pytest tests/test_usage.py -v
```

**手工端到端（必须做一次）**

1. 启动 Web（`python run_web.py`），上传一组多页题图；
2. 在「设置 → 运行状态」确认模型名显示为 `deepseek-flash`；
3. 日志中确认**没有** `reasoning_content`（思考确实关了）；
4. 打开答案卡，人工比对转录文本与原图；
5. 走一次「核对」功能，确认 `verify` 也走的是新模型；
6. **8 图场景（本轮新增的核心验收）**：一个分组窗口内放 8 张互不相关的题图，断言：
   - 日志里**视觉调用只有 1 次**（S8），且无 `finish_reason=length` 告警
   - `timings_json` 的 `polish` 为 **0**（S9）
   - `<OCR_DIR>/<日期>/<task_id>.md` 落盘，且分页数 = 8（S11）
   - 历史页能看到该任务的题目文本（`tasks.problem_text` 已写入）
   - **抽查 LaTeX：`\frac` / `\begin` / `\theta` 没有被转义成控制字符**（S12）——
     这是 JSON 协议下最难发现的一类损坏，换协议后应彻底消失
7. **同一题多页场景（反向用例，不能被跳过）**：放 3 张连续翻页的同一道题，
   确认标记为 `CONT`、**润色未被跳过**（或 `CONT` 页确实做了去重）——
   这是 T3 分流里不能被误跳的分支。

---

## 8. 实测数据（实施时回填）

> **回填状态：全部实测已完成（2026-09-21）**，包括曾经唯一被阻塞的双 provider A/B：
> 用户在 2026-09-20 的 `.env` 事故（第 11.4 节）后先只填回了 `DEEPSEEK_API_KEY`，
> 需要智谱那一腿的 S1/S2 对照当时无法判定；2026-09-21 补回 `ZHIPU_API_KEY` 后
> 5 组真实题图 + 36 个逐图分类样本全部跑完（见 8.4），闸门条件满足，默认 provider
> 已切到 `deepseek`。同一轮还把 8 图合并 vs 并行 vs 分批的耗时对照补齐（8.2/8.3），
> 结果推翻了 §0.4 关于"一次带完 8 张"的默认选择，落地为分批合并（8.6）。

### 8.1 已实测（deepseek-flash，真实 API 调用）

| 指标 | 实测值 | 备注 |
|---|---|---|
| 模型可用性 | `GET /models` → `['deepseek-flash', 'deepseek-v4-pro']` | 计划书 §2.1 的模型名成立 |
| **关思考确实生效** | 带 `thinking.type=disabled`：`reasoning_content=None`，输出 **585** token；不带：`reasoning_content` 非空、`reasoning_tokens=134`、输出 **751** token | 单图 1600×1259 真实题图；**关思考省掉约 22% 输出 token 与全部思考延迟**（S3 的实测证据） |
| 单图 OCR（关思考） | 输入 970 token / 输出 585 token / **4.3 s** | 1 张真实题图，`max_tokens=2048` |
| **2 图合并调用（PAGE 协议）** | **1 次请求 / 5.0 s**，输出 1376 字符，2/2 页，`failed_pages=[]`，`<<<END>>>` 出现 | 两张是**同一道题的第 1、2 页**：模型正确给出 `NEW` + `CONT`，页序未错乱 |
| **LaTeX 静默损坏** | **0**（`\x0c` / `\x08` / `\x09` 均 0；`\frac` 逐字保留） | S12 在真实输出上成立；JSON 协议下同一文本会破坏成 `␌rac{...}` |
| 题型分类 | `ML_CODING`（22 题商品购买预测逻辑回归） | 分类 prompt 新增该标签后，视觉模型直接判出，不再依赖已删除的关键词匹配 |
| 单测覆盖的调用次数 | 8 图 → `classify_and_transcribe` **1 次调用**、8 页、`vision_mode=combined` | S8 由 `test_multi_image_group_merges_by_default` 锁定（打桩，非真实调用） |

**2026-09-21 补测（真实 API，`_probe/ab_images` 的 8 张真实题图，
`python -m tools.vision_ab check -i _probe/ab_images --provider deepseek`）**：

| 指标 | 实测值 | 备注 |
|---|---|---|
| 8 图一次合并调用 | **1 次请求 / 8.7 s**，8/8 页，3392 字符，`failed_pages=[]`，`<<<END>>>` 出现 | `PAGE 协议解析成功：题型=MULTIPLE_CHOICE，8/8 页，结束标记=有` |
| 截断页数 | **0** | A/B 报告判定 ✅ |
| LaTeX 静默损坏 | **0** | 该图组是 Python 单选题截图，**本身不含 LaTeX** → S12 本轮是"空集通过"，证据仍以 8.1 第 5 行的真实 LaTeX 样本为准 |
| 估算费用 | **≈0.0335 元**（8792 in / 1987 out，高峰价 2/8） | 单次 8 图任务 |
| CONT 去重真实发生 | 该图组里模型标了 `CONT` 页（第 3 页是第 2 页的续页），合并文本 3392 字符 < 并行文本 3588 字符 | 差出来的正是被 CONT 去重掉的重复代码块 |

### 8.2 8 图"一次带完 vs 并行 vs 分批"真实耗时对照

命令：`py -3.10 _probe/probe_8images.py _probe/ab_images --repeats 2`
（每边 2 轮，同一组 8 张真实题图，真实 API 调用）

**第一轮测量（2026-09-21 21:41，分批合并尚未实现）**

| 路径 | 第 1 轮 | 第 2 轮 | 中位数 | 请求数 | 输出字符 |
|---|---|---|---|---|---|
| 一次带完 8 张（`COMBINED_VISION_MAX_IMAGES=8`） | 7.79 s | 5.87 s | **6.8 s** | **1**（另 1 轮触发 1 次 `refill`） | 3202 |
| 并行回退（`OCR_PARALLEL_WORKERS=4`） | 3.90 s | 3.41 s | **3.7 s** | **9**（1 分类 + 8 OCR） | 3539 |
| 并行基线（`OCR_PARALLEL_WORKERS=1`） | 12.58 s | — | 12.6 s | 9 | 3561 |

**第二轮测量（2026-09-21 23:0x，分批合并已实现）**

| 路径 | 第 1 轮 | 第 2 轮 | 中位数 | 请求数 | 输出字符 |
|---|---|---|---|---|---|
| 一次带完 8 张（`VISION_BATCH_SIZE=8`） | 8.68 s | 8.18 s | **8.4 s** | 1 | 2749 |
| **分批 2×4 并发（`VISION_BATCH_SIZE=4`，落地形态）** | 6.73 s | 6.11 s | **6.4 s** | **2** | 3267 |
| 并行回退（`OCR_PARALLEL_WORKERS=4`） | 7.92 s | 9.00 s | **8.5 s** | 9 | 3561 |
| 并行基线（`OCR_PARALLEL_WORKERS=1`） | 19.81 s | — | 19.8 s | 9 | 3472 |

**必须诚实标注的两件事**：

1. **同一段代码在两次测量之间的耗时翻了一倍**（并行路径 3.7 s → 8.5 s；并行基线
   12.6 s → 19.8 s）。因此"第一条测量里合并比并行慢 87%"**不足以支撑任何结论** ——
   那更像当时的服务端负载差异，而不是两种调用形态的固有差距。
2. **只有同一轮内的比较才有意义**。第二轮是同一时间窗、交替轮次：分批合并
   **两轮都**快过一次带完（6.73 < 8.68；6.11 < 8.18）**也两轮都**快过并行
   （6.73 < 7.92；6.11 < 9.00）。这才是有带宽意义的证据。

**与路径选择无关、确定成立的差异**：

| 维度 | 一次带完 | 分批 2×4 | 并行回退 |
|---|---|---|---|
| 图片 token 计费 | 1× | **1×** | **2×**（分类一趟 + 8 次单页 OCR） |
| 视觉请求数 | 1 | 2 | 9 |
| 跨页 NEW/CONT 去重 | 全部页 | **批内全部页**（批首按 NEW） | 无（靠润色"场景判断"兜） |
| 单批输出上限压力 | 全 8 页挤一个响应 | 每批 4 页 | 每页 1 段 |
| 实测耗时（第二轮同窗） | 8.4 s | **6.4 s** | 8.5 s |

**结论**：按 §0.4 的判据（合并慢过并行 30% 以上就降级）**两条路都不该选** ——
一次带完确实偏慢，而降到 4 会让 8 图走 9 次请求、图片 token 翻倍、丢失去重。
用户据此拍板选了**分批合并 + 批间并行（组 H2）**，落地形态：8 图 = 2 次请求 ≈6.4 s。
详见 §8.5。

### 8.3 8 图分批合并的端到端实测（生产路径，真实 API）

命令：`py -3.10 _probe/probe_e2e_real.py _probe/ab_images`
（走完整 `SolutionPipeline.run()`：视觉 → OCR 归档 → 文本 → 求解 → 命名归档）

| 检查项 | 实测 |
|---|---|
| 视觉请求 | **2 批**（每批 4 张，2 批并发）+ **1 次单页补做**（某批少返回 1 页） |
| `vision_mode` | `batched`；`ended=True`（两批都出现 `<<<END>>>`） |
| 视觉阶段耗时 | 14.4 s（当前 API 负载下每批 ≈11.5 s；同一个脚本在 23:0x 的对照轮里是 6.4 s） |
| 求解耗时 | 5.0 s（`deepseek-flash`，1876 字符输出） |
| **全流程耗时** | **19.5 s**（迁移前的估算是 90–280 s） |
| S9 `timings.polish` | **0**；无 polish 用量事件（分批内联拼接生效） |
| T4 `timings.filename` | **0 ms**（本地按题号生成：`18-23_Python文件线程类切片迭代.md`） |
| S11 OCR 归档 | `D:\Users\wzw\Pictures\ocr\2026-09-21\e2e-probe-230505.md`，**8 页 == 8 图** |
| S12 | 归档文本控制字符 0、LaTeX 残片 0 |
| 组 I 落库字段 | `problem_text` 3537 字符、`ocr_raw_text` 3537 字符、`vision_mode=batched` |
| frontmatter | `task_id` / `ocr_archive` 均在；`# 题目文本` 小节存在；答案卡抽取成功 |
| 判定 | **16/16 项通过**（第二次复跑同样 16/16：视觉 24.8 s、求解 5.3 s、全程 30.1 s —— 相差的 15 s 完全是服务端负载，功能项与第一次逐条一致） |

> 观察：4 张一批时，模型**有时会漏掉一页**（两次真实分批调用各触发 1 次
> `refill_pages`）。这正是"部分成功即采用 + 单页补做"的价值 —— 补 1 页 = 1 次调用，
> 而不是整批 9 次重来。补做平均多花 ≈2 s。

**第三次复跑（2026-09-22，切换默认 provider 之后的收尾验证）**

节点：`DEFAULT_VISION_PROVIDER` 已改为 `deepseek`、A/B 判据改为
"题目正文"（`extract_body_elements` + `strip_chrome_lines`）、`_startup_cleanup`
补上无主任务 OCR 归档清理之后，重跑同一条生产路径命令
`py -3.10 _probe/probe_e2e_real.py _probe/ab_images --no-archive`：

| 检查项 | 实测 |
|---|---|
| 判定 | **16/16 项通过**（含 S8/S9/S11/S12、`ended` 透出、组 I 两个文本视图） |
| 视觉请求 | **2 批**（每批 4 张并发）+ **1 次单页补做**（第 6 页）；无 `classify`/`ocr` 回退事件 |
| `vision_mode` / `ended` | `batched` / `True` |
| 视觉阶段耗时 | 5.9 s（当前 API 负载下）；求解 6.3 s |
| **全流程耗时** | **12.2 s** |
| S9 `timings.polish` | **0**（且无 polish 用量事件） |
| T4 `timings.filename` | **0 ms**（本地生成：`18-23_Python编程基础与规范.md`） |
| S11 OCR 归档 | `_probe/e2e_out/ocr/2026-09-22/e2e-probe-221042.md`，**8 页 == 8 图** |
| S12 | 控制字符 `\x0c`/`\x08`/`\t` 均为 0、LaTeX 残片 0 |
| 组 I 落库字段 | `problem_text` 3085 字符、`ocr_raw_text` 3085 字符、`vision_mode=batched` |
| 用量事件 | `vision`（8 页, calls=1）+ `vision_refill`（1 页, calls=1）+ `solve`（2581 字符） |

> 与前两次的差别只有服务端负载（视觉 5.9–24.8 s），功能项逐条一致；
> "补 1 页"再次出现，说明 `refill_pages` 在多批次里是**常态而非异常路径**。

**第四次复跑（2026-09-22 稍晚，计费口径修复之后）**：同样 16/16 通过，
用量事件里已带上 `images` 字段（`vision` → 8、`vision_refill` → 1、`solve` → 0），
即 `webapp/usage.py` 现在拿到的是"真实上传了几张图"而不是"页数 × 请求数"。
该轮 **求解阶段耗时 75.5 s**（此前三轮为 5.0 / 5.3 / 6.3 s）——阶段耗时里
`solve` 是唯一受服务端负载与两段式思考升级共同影响的量，**视觉与转录部分
（6.6 s）依旧稳定**。功能判定不受影响。

### 8.4 双 provider A/B 判定（2026-09-21，`ZHIPU_API_KEY` 补回后完成）

命令（工具会按 `--reference` 自动补跑基准那一家）：

```powershell
py -3.10 -m tools.vision_ab check -i <图或目录> --provider deepseek --reference zhipu
```

#### S1：OCR 不倒退（要素比对）

5 组真实题图（8 张/组，共 40 张；deepseek 为候选、**zhipu 为基准**）：

| 组 | 题目正文要素缺失 | 全页要素缺失（诊断） | 正文多出要素 | 字符数差 | 截断 | LaTeX 损坏 | deepseek / zhipu 耗时 |
|---|---|---|---|---|---|---|---|
| `_probe/ab_images` | **0** | 11 | 14 | +345 | 0 / 0 | 0 / 0 | 26.8 s / 52.3 s |
| 同上（修正判据后复跑） | **0** | 2 | — | — | 0 / 0 | 0 / 0 | **6.6 s / 38.0 s** |
| `_probe/ab_group2` | **0** | 0 | 28 | +1129 | 0 / 0 | 0 / 0 | 8.8 s / 35.9 s |
| `_probe/ab_group3` | **0** | 0 | 7 | +843 | 0 / 0 | 0 / 0 | 11.0 s / 24.9 s |
| `_probe/ab_group4` | **0** | 0 | 13 | +467 | 0 / 0 | 0 / 0 | 6.6 s / 51.0 s |

**判定：S1 ✅ 不倒退**（题目正文要素缺失 0/4 组，且候选一致地"多出" 7–28 个要素、
字符数 +345…+1129）。

> **判据修正记录（重要，必须留痕）**：第一版判据把**页眉/题号**也算成"题干数字"——
> `extract_elements` 抓的是全页数字，于是试卷 ID `78060`、`满分：135分 及格：115分 已答`、
> `第18/60题` 全被计入。第一组因此报"缺失 11 个"，逐条核对后**全部**是页眉数字
> （两家各自在不同页省略页眉，方向还不一致：候选同时"多出" 24 个同类数字）。
> 因此判据改为**先剥离页眉/页脚/题号导航**（S1 说的是"题目要素（题干、选项、数字、公式）"），
> 并把**全页计数**与被剥离的行样例同时写进报告（`要素缺失数（全页…）` + `chrome_samples`），
> 剥离过程完全可核对、不做隐藏。人工逐页复核结论与之一致：
> - 题目 18/19/21/22/23、选项 A–D、代码块在两家都完整；
> - 有 4 处**zhipu 反而不忠实**：`print(language[:-4])` 被写成 `print(language[-4])`（丢冒号，
>   语义变了）、第 19 题的代码块漏行且把 `t.join()` 重复两次、`data={"Name":123"...}` 的
>   `data=` 关键字被丢掉、第 21 题四个选项里三个被写成同一个 `self.dict`（deepseek 保留了
>   四个不同写法）。
> 复核命令（不花 API 费用，直接对已保存的逐页文本重算）：`py -3.10 _probe/rescore_s1.py`。

#### S2：题型分类一致率

A/B 工具每次运行只产出**1 个题型**（一组图 → 1 次分类），因此另外用
`_probe/probe_classify_agreement.py` 对**逐张图**做独立分类，得到真正可算一致率的样本：

| 样本来源 | 样本数 | 一致 | 一致率 |
|---|---|---|---|
| `webapp/cache/images`（每 15 张取 1） | 12 | 12 | 100% |
| `_probe/ab_group2`（含 CODING / ML_CODING / GENERAL） | 8 | 8 | 100% |
| `_probe/ab_group3` | 8 | 7 | 87.5% |
| `_probe/ab_group4` | 8 | 8 | 100% |
| **合计** | **36** | **35** | **97.2%** |

**判定：S2 ✅ 满足（≥90%）**。混淆矩阵（zhipu → deepseek）：
`MULTIPLE_CHOICE→MULTIPLE_CHOICE` 30、`ML_CODING→ML_CODING` 2、`GENERAL→GENERAL` 3、
`CODING→CODING` 1、**`CODING→ML_CODING` 1（唯一分歧）**。

> **组级一致率不可用作 S2 判据**：A/B 工具按"整组 8 张"聚合出一个题型，而一组图可能混着
> 编程题与选择题（`ab_group2` 就是 5 选择 + 1 编程 + 1 ML + 1 通用），此时"该组是什么题型"
> 本身没有唯一答案 —— 同一次运行里 deepseek 给 `MULTIPLE_CHOICE`（占多数）、zhipu 给 `CODING`。
> 这不是分类错误，而是聚合口径问题；逐图分类才是 S2 该量的东西。

#### 代价（C1）与回退

- **视觉单价高约 4.7 倍**：8 图任务 ≈0.033–0.037 元（deepseek）vs ≈0.006–0.007 元（zhipu，
  按生产 `COST_TABLE` 的 `GLM-4.6V-FlashX` 0.5/1.5 计）；
- **换来 3.8–7.7 倍的视觉耗时优势**（6.6–26.8 s vs 24.9–52.3 s，同一轮对照）；
- 回退是一条环境变量：`VISION_PROVIDER=zhipu` + `ZHIPU_API_KEY`，行为与迁移前逐字节一致
  （S5 有单测锁定，包括 GLM 的 8192 输出上限）。

**结论：闸门通过 → 默认 provider 已由 `zhipu` 切到 `deepseek`**（§6 第 5 步）。

### 8.5 其余单 provider 指标（历史回填；已被 8.2–8.4 覆盖的项保留作对照）

> 本表在 2026-09-20 首次回填，当时 `ZHIPU_API_KEY` 尚缺。要素缺失 / 一致率 / 截断 /
> 8 图请求数 / 端到端延迟这几项此后已由 §8.2–§8.4 用**双 provider 真实数据**取代；
> 保留它们是为了留下"从估算到实测"的痕迹，避免把旧估算当成结论引用。

| 指标 | GLM-4.6V-FlashX | deepseek-flash | 判定 |
|---|---|---|---|
| 8 图转录字符数 | 2544–3086（实测） | **2037–4215**（实测） | deepseek 一致更多，见 8.4 |
| 题干/选项/公式要素缺失数 | 基准 | **0**（题目正文，4 组） | ✅ 见 8.4 |
| 题型分类一致率 | 基准 | **97.2%**（36 个逐图样本） | ✅ 见 8.4 |
| `finish_reason=length` / 缺 `<<<END>>>` 页数 | **0** | **0** | **必须为 0** ✅ |
| **8 图任务：视觉请求数** | 2（分批） | **2**（分批实测，非打桩） | S8 = ceil(N/4)，见 11.2 |
| **8 图任务：API 调用总数** | 3–4 | 2（视觉）+ 1（求解）+ 1（补页，视情况）= 3–4 | 记录即可 |
| **8 图任务：端到端延迟** | 24.9–52.3 s（仅视觉） | **6.6–26.8 s**（仅视觉）；端到端 19.5–30.1 s | 见 8.2 / 8.3 / 8.4 |
| **8 图任务：润色是否触发** | 必触发 | **0**（内联拼接，端到端实测） | **0**（S9） |
| **PAGE 协议成功率** | 11/11（分批） | 10/10 次真实合并调用解析成功；期间 2 次触发 `refill` 补页，1 次因底座 bug 走 JSON 回退（已修） | 记录即可 |
| **`refill_pages` 触发率** | ~2/6 | 约 2/6 次真实分批/合并调用各缺 1 页 → 各补 1 次 | 补页路径真实可用 |
| 8 图估算费用（元） | 0.006–0.007 | 0.033–0.037 | C1：贵约 4.7 倍，见 8.4 |

### 8.6 组 H2 落地：分批合并 + 批间并行（用户 2026-09-21 拍板）

配置（`problem_solver_agent/config.py`）：

```python
VISION_BATCH_SIZE = int(os.getenv("VISION_BATCH_SIZE", "4"))      # 单批最多几张图
VISION_BATCH_WORKERS = int(os.getenv("VISION_BATCH_WORKERS", "4"))  # 批间并行度
```

实现（`vision_client.py`）：

- `_combined_once(batch, provider, model_name)`：**一批**的 PAGE→JSON 两段回退，不做补页；
- `_merge_batch_results(batch_lists, results)`：按下标写回全局页序（并发返回顺序随机，
  不能按完成顺序）、批首页强制 `NEW`、任一批失败/无接缝则 `seams=False`、
  `ended` 取各批合取、题型取**多数**（并列取最早出现）；
- `classify_and_transcribe()`：按 `VISION_BATCH_SIZE` 切批，`ThreadPoolExecutor`
  并发，最后统一 `refill_pages()`（补页仍是单页请求、按 `OCR_PARALLEL_WORKERS` 并行）；
- 契约新增两个字段：`calls`（真实请求数）与 `seams`（页面间是否存在可信 NEW/CONT）。
  `core_pipeline._textualize(seams=...)` 用它决定是否内联——**不能用 `vision_mode`
  代替**：分批里只要有一批走了 JSON，就不能按"有接缝"内联。

**计费口径**：`_emit_usage(stage="vision")` 的 `calls` 仍为 **1**（它是**计费放大倍数**：
输入 token 按"每图 1024"折算，每张图只上传一次；乘批数会把视觉成本算成 N 倍）。
真实请求数只记日志。A/B 报告另有"调用路径/请求数"栏位。

**为什么不是"降到 4 走并行"**（§0.4 的原始判据）：那条路 8 图 = 9 次请求、
图片 token 付两次、且没有跨页去重。分批合并用同样"每图只上传一次"的成本拿到
与并行相当的耗时，因此是更优解。

> **上面这句"耗时相当"已被 §8.7 的交替轮次复测修正**：并行其实稳定快约 2.3 s
> （3.71 s vs 6.01 s，4/4 cycle 全胜）。但并行路径的成本高近一倍
> （prompt_tokens 17155 vs 8792，服务端实测），且分批路径的"每图只上传一次"
> 优势是真实的。**默认值因此维持 4，但依据从"耗时相当"改为"成本 + 补页率"** —— 见 §8.7。

### 8.7 交替轮次复测与真实 token 成本（2026-09-22，修正 §8.2/§8.6 的耗时前提）

§8.2 的成块测量（先跑完 3 轮 A，再跑 3 轮 B，再跑 3 轮 C）在服务端负载漂移下
**不足以支撑两秒级的结论**。第三次复跑就撞上了这一点（1×8 中位数 6.5 s、
分批 8.2 s、并行 8.7 s，与 §8.6 的"分批两轮都更快"相反）。因此改用**交替轮次**：
A/B/C 各一轮算一个 cycle，连续 4 个 cycle，用"每个 cycle 内谁最快"做配对比较，
以抵消同一时间窗内的负载漂移。

命令：`py -3.10 _probe/probe_batch_interleaved.py _probe/ab_images --cycles 4`

| 形态 | 4 轮原始值 | 中位数 | 请求数 | 补页 | 每 cycle 最快 |
|---|---|---|---|---|---|
| A 一次带完 1×8 | 7.66 / 5.34 / 5.74 / 5.25 | **5.54 s** | 1 | 1/4 轮 | 0/4 |
| B 分批 2×4（当前默认） | 5.79 / 5.68 / 6.37 / 6.23 | **6.01 s** | 2 | **4/4 轮各补 1 页** | 0/4 |
| C 并行 workers=4 | 3.74 / 3.85 / 3.68 / 3.43 | **3.71 s** | 9 | 0 | **4/4** |

**结论一：并行回退路径在耗时上稳定胜出** —— 同 cycle 内 **4/4 全胜**，方差也最小
（3.43–3.85 s）。§8.6 那句"分批与并行耗时相当"**不成立**，那是成块测量造成的假象。

**结论二：但并行路径的成本约为合并路径的 2 倍。** 原因是它的调用结构 ——
分类那一次**必须带全部 N 张图**（而它只产出 1 个题型标签），逐页 OCR 再各带 1 张。
用服务端返回的 `usage`（真实计费口径，不是 `webapp/usage.py` 的估算）实测：

命令：`py -3.10 _probe/probe_vision_cost.py _probe/ab_images`

| 指标 | 分批合并 2×4 | 并行 workers=4 | 并行 / 分批 |
|---|---|---|---|
| 请求数 | 2 | 9 | **4.50×** |
| 图片上传张次 | 8 | 16 | **2.00×** |
| prompt_tokens（服务端实测） | 8792 | 17155 | **1.95×** |
| completion_tokens | 1415 | 1580 | 1.12× |

按高峰单价（2 / 8 元每百万）折算：**分批 ≈0.029 元 vs 并行 ≈0.047 元**（8 图任务）；
而并行的输出量并没有更多（1580 vs 1415）—— **多付的 60% 全花在那趟"为了拿一个
题型标签而重传 8 张图"的分类调用上**。

**因此默认值维持 `VISION_BATCH_SIZE=4`（分批合并）**：耗时比并行慢约 2.3 s，
但省下近一半成本、少 7 次请求。另有一个此前没被记录的差异：**分批 4/4 轮都触发了
单页补做，而并行 0 次** —— 分批把 8 页挤进 2 个响应，模型更容易漏页（每次补做
≈1 次单页调用 + 1–3 s），这正好抵消了一部分耗时优势。

> **决策已拍板（2026-09-22，用户确认）**：**保持分批合并（成本优先）**。
> 理由：8 图任务里视觉只占端到端的一小段（求解才是大头），用 60% 的视觉成本
> 换 2.3 s 不划算；且分批少 7 次请求、失败面更小。
> **该结论不是"技术上分批更快"** —— §8.2 的原始判据（"合并慢过并行 30% 就降级"）
> 在耗时维度上其实**应当降级**，本项目改用**耗时 / 成本 / 补页率**三维度判定，
> 并把权重放在成本侧。若将来改成延迟优先，`USE_COMBINED_VISION_CALL=false`
> 一行即可切到并行（代价见上表）。

> **本节关闭 §11.6 第 8 项**（"分批 vs 并行 vs 一次带完的耗时受负载影响大、
> 默认值的证据强度有限"）：交替轮次 + 配对比较解决了成块测量的"块内自相关"
> 方法论问题，两个新探针脚本（`_probe/probe_batch_interleaved.py` /
> `_probe/probe_vision_cost.py`）都可复跑。

---

## 9. 假设与未决问题

**假设**

- **A1**：`deepseek-flash` 的视觉能力在本题库（中文数学题截图）上**可用但不保证优于**
  `GLM-4.6V-FlashX` —— 故以 A/B 结果为准，本文档不预设结论；
- **A2**：DeepSeek 已下线 `deepseek-v4-flash-vision-exp`，`deepseek-flash` 是其正式承接者，
  接口契约长期稳定；
- **A3**：`IMAGE_MAX_EDGE=1600` 与 `IMAGE_JPEG_QUALITY=80` 保持不变（避免一次改两个变量，
  无法归因）；
- **A4**：迁移不涉及 GPU / 本地模型，仍然是纯 API 方案；
- **A5**：`VISION_PROVIDER=zhipu` 的回退分支长期保留；
- **A6**：视觉模型在合并调用里能可靠区分「同一题的多页」与「多道独立的题」
  （落地形态是每页块上的 `NEW` / `CONT` 标记，**不是**设计稿里的 `LAYOUT` 字段）
  —— 这一条**必须由 A/B 验证**，判错的代价是丢掉本该做的去重。
  已有一处真实证据：2 张同一道题的连续页，模型正确给出 `NEW` + `CONT`（见 8.1）；
- **A7**：多图合并调用的输出在 384K 之内（8 页转录约 3–10K token，余量充足）；
- **A8**：`OCR_PARALLEL_WORKERS` 只影响回退路径（合并调用成功时是单请求，不走线程池）。

**未决问题（实施前需要用户拍板）**

| # | 问题 | 影响 |
|---|---|---|
| Q1 | 如果 A/B 显示 OCR 退化，接受吗？ | 决定是「切换」还是「终止迁移」 |
| Q2 | 成本上涨是否可接受？（OCR 输入贵 2–4 倍） | 决定是否需要保留混合方案：**OCR 用智谱 FlashX，推理/核对用 DeepSeek** |
| Q3 | 既然隐私是真实诉求，要不要**另起**一个「本地视觉模型」方案？ | 本迁移不解决隐私；这是唯一能解决的路径 |
| Q4 | 是否接受 C5 的**单点依赖**（DeepSeek 挂了全流程挂）？ | 决定 `zhipu` 回退分支是「长期保留」还是「临时过渡」 |
| Q5 | 多图合并调用是「全有或全无」，失败即整体回退到 9 次串行调用。这个失败率可接受吗？ | 若偏高，需要把 `COMBINED_VISION_MAX_IMAGES` 调低（如 4），或改成「分批合并」 |

---

## 10. 隐私事实（独立章节，供决策参考）

> 本节与迁移技术方案无关，但它是本次需求的**起点**，必须留在文档里。

1. **Base64 不是加密。** 它是把二进制转成 ASCII 的编码方案，无密钥、可逆、
   任何中间方一秒可解。发送 `data:image/jpeg;base64,...` 等价于发送原图本身。
2. **厂商能看到完整原图。** 解码发生在服务端，发生在模型推理之前。
   模型看到的是像素，不是编码字符串。
3. **换供应商 ≠ 保护隐私。** 本次迁移把接收方从智谱换成 DeepSeek，
   暴露面**不变**。若隐私是硬需求，唯一解是本地推理（见 0.1 表格）。
4. **已做到的（既有能力）**：`image_prep.py` 的 EXIF 校正丢弃元数据
   （GPS / 设备 / 时间）—— 保护元数据，不保护图像内容。
5. **建议的补充手段**（若隐私是硬需求）：在 `image_prep.prepare_image_bytes`
   的 EXIF 校正之后、缩放之前，插入一步**本机敏感区域打码**
   （姓名 / 手机号 / 学号 / 身份证区域），复用 `watermark.py` 已验证的
   「掩膜 + 闸门 + 失败即回退原图」模式。这是一个**独立的、与本迁移正交**的方案。

---

## 11. 实施记录（回填）

> 本节记录**实际落地**的形态与设计稿的差异、验收结果、以及未完成项。
> 第 0–10 节保持为"设计意图"原文，施工期被改写的旧值已在原处标注（T2 的 3→4、
> `VISION_MAX_TOKENS` 的 8192→32768、`LAYOUT`/`_pages.json` → PAGE 标记 + `.md` 归档）。

### 11.1 已落地（按迁移文档的分组）

| 组 | 内容 | 落点 |
|---|---|---|
| A | provider 表 + 派生常量 + `VISION_API_KEY` + 新可调项 | `problem_solver_agent/config.py` |
| B | 按 provider 建 client；`extra_body={"thinking":{"type":"disabled"}}`；`max_tokens` 读配置；视觉推理按 provider 分支 | `vision_client.py` |
| C | `COST_TABLE` 修正（`deepseek-flash` 2.0/8.0、新增 `deepseek-v4-pro` 9.0/27.0）；`TOKENS_PER_IMAGE` 700→1024；`estimate_task_cost` 跟随当前视觉模型 | `webapp/accounts.py`、`webapp/usage.py` |
| D | 校验链统一到 `config.VISION_API_KEY`，错误文案带 provider 与所需环境变量名 | `main.py`、`pipeline.py`、`webapp/app.py`、`webapp/routes.py`、`tools/diag.py` |
| E | `diag` 视觉层汇总行；`.env.example` 重写；`start_web.bat` 按 provider 判密钥 | 同名文件 |
| F | **`tools/vision_ab.py`**（新建）：双 provider A/B，要素差 / 一致率 / 截断 / LaTeX 损坏 / 耗时 / 费用，出 `report.md` + `result.json` | `tools/vision_ab.py` |
| G | `tests/test_vision_provider.py`（新建）锁定 S3/S5；PAGE 协议全套用例；S6/S10 断言；`test_vision_client.py` / `test_verify.py` 去硬编码模型名 | `tests/` |
| H | T1 上限 1→8 + PAGE 协议 + 三级回退 + `refill_pages`；T2 并行度 1→4；T3 内联润色 + 润色 prompt 场景判断；T4 文件名三档 + `FILE:` 首行 + 计时计费；T5 辅助链路关思考 + `AUX_TIMEOUT` | `config.py`、`prompts.py`、`vision_client.py`、`solver_client.py`、`core_pipeline.py` |
| **H2** | **分批合并 + 批间并行**（2026-09-21 实测后追加）：`VISION_BATCH_SIZE`(4) / `VISION_BATCH_WORKERS`(4)、`_combined_once` / `_merge_batch_results`、`calls`+`seams` 契约、`_textualize(seams=...)` | `config.py`、`vision_client.py`、`core_pipeline.py` |
| I | `<OCR_DIR>/<日期>/<task_id>.md` 归档（视觉阶段后立刻写）；frontmatter 加 `task_id`/`ocr_archive`；`tasks` 三列 + `search_tasks` + `GET /api/tasks?q=` | `core_pipeline.py`、`webapp/models.py`、`webapp/routes.py`、`webapp/pipeline.py` |
| **C2** | **图片 token 计费口径**（2026-09-22，§11.6-5）：`UsageReport.images`（实际上传图片数，与 `calls` 解耦）+ `estimate_tokens(..., calls=, images=)` 分开算固定开销与图片；纯文本阶段上报 `images=0` | `core_pipeline.py`、`webapp/usage.py`、`tests/test_usage.py` |
| **I2** | **无主任务的 OCR 归档清理**（2026-09-22，§11.6-1）：`_startup_cleanup` 对孤儿 id 调 `delete_ocr_archives` | `webapp/app.py`、`tests/test_retention.py` |
| — | 清理：`ML_CODING` 上移到分类 prompt（视觉模型直接判，比关键词匹配准），删除死代码 `reclassify_problem_type` 与两个 KEYWORDS 表 | `pipeline.py`、`config.py`、`prompts.py` |

### 11.2 与设计稿的差异（都是有意的）

1. **`ended` 进了返回契约**：`classify_and_transcribe` 返回 `ended`（响应里有没有
   `<<<END>>>`）。流式下拿不到 `finish_reason`（`_call_vision_api(stream=True)` 只 yield
   文本），缺 `<<<END>>>` 就是**协议级**的截断信号，比 `finish_reason` 更可靠。
   A/B 工具因此改为读生产返回值，不再 monkeypatch `_collect_stream`。
2. **新增 `stage="vision_refill"` 用量**：`refill_pages` 的单页补做是真金白银的调用
   （补 2 页 = 2 次请求）。没有把它并进 `vision` 的 `calls`，因为 webapp 记费会按
   `pages × 1024 token × calls` 算，并进去会让补做成本被平方级高估。
3. **`LAYOUT` 字段不存在**：版面判断就是每页的 `NEW`/`CONT` 标记（设计稿 0.4 节自己
   论证过它更强）；`{task_id}_pages.json` 也不存在，归档是 `.md`（人可读 + frontmatter
   已带结构化字段）。
4. **`COMBINED_VISION_MAX_IMAGES=8` 的含义变了**：它仍是"合并调用的适用范围上限"，
   但**单次请求最多带几张图**由新的 `VISION_BATCH_SIZE=4` 决定 —— 因此 8 图 =
   2 次并发请求（组 H2）。2026-09-21 的真实对照显示一次带完 8 张是最慢的形态之一
   （同窗 8.4 s vs 分批 6.4 s），原设计稿"8 图 = 1 次请求"的默认因此被改写。见 §8.2/§8.5。
5. **`OCR_PARALLEL_WORKERS=4`**（设计稿 0.4 节写 3、组 H 写 4 —— 取组 H 的 4；它只影响
   回退路径，约束是本机 JPEG 解码而非 API 并发）。`refill_pages` 的补页也复用这个并行度。
6. **越界页码语义**：模型声明 `<<<PAGE 9>>>` 而只有 8 张图时，该页块被丢弃并记 WARNING；
   **不**记入 `failed_pages`。理由：真正缺的页（1–8 里没被声明的）本来就是空页、已经
   进 `failed_pages`，因此不会掩盖缺页；多记一条只会让 `refill_pages` 去补一个不存在的下标。
   用例 `test_out_of_range_page_number_is_dropped_not_recorded` 锁定了这个行为。
7. **前端仅加了三个可选类型字段**（与设计稿"前端无需改动"一致）：`problem_text` /
   `ocr_raw_text` / `vision_mode` 是可选字段，界面不消费它们（历史页仍按题型 + 文件名展示），
   加类型只是让 REST 契约与 `docs/API.md` 对齐；纯类型改动，无运行时行为变化、无需重建产物。
8. **PAGE 标记容忍缺失与写错的 flag**：`<<<PAGE 1>>>`（没写 `|NEW`）与
   `<<<PAGE 1|NEWY>>>`（写错）都接受，按计划书"标记缺失或非法即按 NEW"处理。
   早期正则强制 `|NEW`/`|CONT` 精确匹配，畸形标记**整块不匹配** → 它的正文落进上一页的
   切片，造成"上一页被塞进两页内容 + 该页槽位为空触发 refill"的**静默重复**（审计发现，已修）。
9. **页码整体平移（0 基编号）判为不可信**：出现 `<<<PAGE 0>>>` 时整段返回 None 并回退。
   平移会让每页错位一格、第 1 张图的正文永久丢失，且无法与"合法跳号"区分（审计发现，已修）。
   `<<<END>>>` 之后的内容对**每一页**截断，而不只是最后一页。
10. **阶段缓存指纹从"模型名"扩到四项**：`model` / `provider` / `max_tokens` / `protocol`
    （`_CACHE_PROTOCOL = "page-v2"`）。只比对模型名时，"把 `VISION_MAX_TOKENS` 调大修截断"
    之后点重试仍会命中那份被截断的转录 —— 计划书 §5 的补救措施会静默失效（审计发现，已修）。
11. **`provider=` 参数真正贯通**：`_combined_once` / `classify_problem_type` /
    `transcribe_images` / `classify_and_transcribe_parallel` / `refill_pages` 全部接受并透传
    provider，模型名走 `_model_for(provider, kind)`。此前模型名读的是**默认 provider** 的
    派生常量，`provider="zhipu"` 会把 `deepseek-flash` 发到智谱端点（审计发现，已修）。
12. **输出上限按 provider 分支**：provider 表新增 `max_tokens`（deepseek 32768 / zhipu 8192），
    `VISION_MAX_TOKENS` 环境变量只覆盖**当前** provider。全局用一个值会让回退路径给 GLM 发
    32768（GLM 上限 8192），S5 的"与迁移前逐字节一致"就不成立（审计发现，已修）。
13. **上传清理加了数据丢失护栏**（事故衍生，见 11.4）：`prune_uploads()` 在保留集合为空时
    默认拒绝删除；`tests/conftest.py` 的 autouse 夹具把所有"会删文件"的路径默认指向 `tmp_path`。

### 11.3 验收结果（S1–S12）

| # | 标准 | 状态 | 依据 |
|---|---|---|---|
| S1 | OCR 不倒退 | ✅ | 双 provider A/B，基准 zhipu、候选 deepseek，5 组共 40 张真实题图（§8.4）：**题目正文**要素缺失 **0**，候选一致地多出 7–28 个要素、字符数 +345…+1129；截断 0、LaTeX 静默损坏 0。判据修正与人工逐页复核记录见 §8.4，重算命令 `py -3.10 _probe/rescore_s1.py`（不花 API 费用） |
| S2 | 分类一致率 ≥90% | ✅ | 36 个**逐图**独立分类样本一致率 **97.2%**（35/36），唯一分歧是 `CODING→ML_CODING`（§8.4）。组级一致率不可用作判据（一组图可能混合多种题型，聚合口径本身没有唯一答案） |
| S3 | 视觉调用关思考 | ✅ | 单测断言 payload（`test_deepseek_payload_disables_thinking`）+ 真实调用实测 `reasoning_content=None`（8.1） |
| S4 | 现有 pytest 全绿 | ✅ | 全量 `pytest -o addopts=""` → **478 passed**（2026-09-22 收尾复核；2026-09-21 为 458，此后新增 A/B 判据剥离页眉 4 条、无主任务 OCR 归档清理 2 条、**计费口径 14 条**）；前端 `vitest` 158 passed、`tsc --noEmit` exit 0 |
| S5 | `VISION_PROVIDER=zhipu` 一键回退 | ✅ | `test_zhipu_payload_has_no_extra_body`（含输出上限 8192）、`test_zhipu_env_derives_glm_models`（子进程）、`test_zhipu_config_table_matches_pre_migration` |
| S6 | 成本可见（单价真实） | ✅ | `test_cost_table_reflects_migration_prices` |
| S7 | 配置自检报明确错误 | ✅ | `py -3.10 -m tools.diag` 输出 `provider=deepseek 模型=deepseek-flash 思考=关闭 密钥=已配置`（缺密钥时点名 `DEEPSEEK_API_KEY`） |
| S8 | 8 图视觉请求数 = ceil(N/VISION_BATCH_SIZE) = **2** | ✅ | **真实 API 端到端实测**（8.3）：2 批 + 1 次单页补做；单测 `test_multi_image_group_merges_in_batches` 锁定分批与页序 |
| S9 | 合并路径 `timings.polish == 0` | ✅ | 单测 `test_inline_merge_skips_polish_on_combined_path` / `test_batched_transcript_inlines_and_skips_polish` / `test_batched_without_seams_keeps_the_polish_call`；**真实端到端**实测 `polish=0` 且无 polish 用量事件（8.3） |
| S10 | 辅助链路关思考 | ✅ | `test_ask_for_analysis_disables_thinking`、`test_ask_for_analysis_timeout_comes_from_aux_timeout` |
| S11 | 转录已归档且页数一致 | ✅ | 单测 `test_ocr_archive_is_written_page_per_image` / `test_ocr_archive_survives_solve_failure`；**真实端到端**归档 8 页 == 8 图（8.3） |
| S12 | 无 LaTeX 静默损坏 | ✅ | PAGE 协议不再经过 JSON 转义层；真实输出 `\frac` 逐字保留、控制字符 0（8.1）；真实端到端归档控制字符 0（8.3）；`test_preserves_latex_backslashes` 是回归保护 |

### 11.4 事故：仓库 `.env` 被覆盖，密钥丢失（未完全恢复）

- **发生了什么**：实施期间 `D:\Users\wzw\Pictures\OnlineTest\.env` 被 `copy .env.example .env`
  覆盖成与 `.env.example` 字节完全一致（同 9798 B、同 mtime）。`DEEPSEEK_API_KEY` /
  `ZHIPU_API_KEY` / `MOONSHOT_API_KEY` / `MINIMAX_API_KEY` / `DASHSCOPE_API_KEY` /
  `TAVILY_API_KEY` 的真实值随之丢失。
- **触发路径**：为验证 `start_web.bat` 的配置段而实际执行了该脚本；脚本末尾会真启动
  `run_web.py`。脚本里的 `copy /y .env.example .env` 是唯一能产生"字节级一致"的分支。
  无法 100% 断定，但时间线与字节一致性都指向它。
- **为什么恢复不了**：`.env` 从未进 git（被 `.gitignore` 忽略）；回收站无副本；全盘
  文件名搜索无 `.env` 副本；对本会话与历史会话的日志做 `sk-` 内容搜索无命中（密钥从未
  被任何工具调用完整打印过，这也是当初刻意遮掩的结果）；VSS 影子副本需要管理员权限。
- **已做的补救**：
  1. `.env` 顶部写入醒目的"事故恢复说明"，列出需要填回的变量；
  2. **`start_web.bat` 不再自动 copy `.env`** —— 缺失时只打印指引并退出，
     用一步便利换掉一个不可逆的事故面（这是本次事故唯一能"根治"的部分）；
  3. `tools/diag` 与启动日志在缺密钥时明确报出"缺哪个环境变量、当前 provider 是什么"。
- **需要用户做的事**：把 `DEEPSEEK_API_KEY`（必填）与 `ZHIPU_API_KEY`（跑 A/B 对照与
  回退时需要）填回 `.env`，然后执行 8.2 的两条命令补完 S1/S2/S8 的实测。
- **附带损失与随之修掉的真实缺陷**：`webapp/uploads/*/` 下的原图被整批清理。
  排查结论（有证据，不是猜测）：
  1. 这 8 个上传目录**都对应真实 DB 里的任务**（`tasks` 表能一一对上），
     所以"用真实 DB 启动服务"的正常路径**不会**删它们；
  2. 删除必然发生在**任务库为空 / 不是这一份**的上下文里 —— 而
     `prune_uploads(upload_dir, keep_task_dirs)` 把这两个入参当成互相独立的参数，
     调用方极易配错（测试的 tmp 任务库 + 全局上传目录、探针脚本自造库、
     `DB_PATH` 被覆盖的第二个实例），配错的表现恰好就是**空保留集合**；
  3. 具体是哪一次调用触发，无法从现场复原（进程已退出、无日志留存）——
     时间线上与"为验证 `start_web.bat` 而实际启动服务/冒烟"重合，但**不能坐实**。
  因此按"结构性隐患"处理，已落地三处修复（比归因到某一次调用更有价值）：
  - `webapp/retention.prune_uploads()`：**保留集合为空时默认拒绝删除**并告警，
    确实要清空的调用方须显式传 `allow_empty_keep=True`（删除不可逆，宁可少清理）；
  - `webapp/app._startup_cleanup()`：显式接受 `upload_dir` 参数，任务库为空时只告警不删；
  - `tests/conftest.py` 新增 autouse 夹具：把所有"会删文件"的路径
    （`UPLOAD_DIR` / `SOLUTION_DIR` / `DATA_DIR` / `DB_PATH` / `IMAGE_CACHE_DIR` / `OCR_DIR`）
    默认重定向到 `tmp_path` —— 把"忘打补丁"的代价从"不可逆删用户数据"降为"多测一个空目录"。
  验证：4 条新回归用例（`tests/test_retention.py`）+ 一次**哨兵实验** ——
  在真实 `webapp/uploads/` 下放一个哨兵目录后跑完整 pytest，哨兵存活。
  A/B 改用 `webapp/cache/images` 里 181 张已预处理的真实题图（`_probe/ab_images/` 8 张）。
- **2026-09-21 更新**：用户已填回 `DEEPSEEK_API_KEY`（只填了这一个），因此单 provider 的
  真实调用全部可跑（见 8.1/8.2/8.3）。
- **2026-09-21 晚更新**：`ZHIPU_API_KEY` 也已补回，双 provider 的 S1/S2 对照跑完并判定
  通过（§8.4），默认 provider 随即由 `zhipu` 切到 `deepseek`（§6 第 5 步）。
  **本次事故至此全部收口**：密钥补回、`start_web.bat` 不再自动 `copy .env`、
  上传目录清理的三处护栏与哨兵实验均已落地。

### 11.5 独立审计与修复（2026-09-21，两个只读审计）

迁移落地后由两个独立审计分别复核了**视觉层**（`vision_client.py` / `prompts.py` /
`config.py`）与**流水线层**（`core_pipeline.py` / `webapp` / `tools/vision_ab.py`）。
审计只读、不改代码；下面每一条都已修复并带回归用例（**未修复项见 11.6**）。

| # | 严重度 | 缺陷 | 修复 |
|---|---|---|---|
| A1 | **阻断** | `provider=` 半接线：模型名读默认 provider 的派生常量，`provider="zhipu"` 会把 `deepseek-flash` 发到智谱端点；`classify/parallel/refill` 根本不接 provider → **A/B 工具的双 provider 腿无效** | `_model_for(provider, kind)` + provider 全链路透传（`_combined_once` / `classify_problem_type` / `transcribe_images` / `classify_and_transcribe_parallel` / `refill_pages`） |
| A2 | 高 | `max_tokens` 不按 provider 分支：回退到 GLM 也发 32768（GLM 上限 8192）→ S5 的"逐字节一致"不成立 | provider 表加 `max_tokens`；`config._vision_max_tokens(provider)`；环境变量只覆盖当前 provider |
| B1 | 高 | 阶段缓存**读侧**没跟上新包装：`routes.resolve` 读 `cached.get("pages")` 恒为 None → 求解失败后重解必然 409 `no_transcript`（"别把 OCR 再买一次"的修复失效） | `webapp/pipeline.read_cached_transcript()`（拆包 + 模型/provider 失效判定），`routes` 改走它 |
| B2 | 高 | A/B 工具**假 PASS**：并行腿无条件 `ok=True`，零输出也能打印"✅ 满足验收标准" | `ok` 改为"真的有可用页"；空基准/空题型/未知截断信号一律判"无法判定"而非通过 |
| B3 | 中 | A/B 截断闸门不可能失败：漏掉 `<<<END>>>` 但每页非空时 `截断页数=0` → PASS | 协议级 `ended=False` 强制 `truncated_pages>=1`；并行腿的未知信号不再判通过 |
| A3 | 中 | PAGE 畸形 flag（`\|NEWY`）整块不匹配 → 正文落进上一页，**静默重复** | flag 放宽为任意字母 token，非法值按 NEW |
| A4 | 中 | 0 基编号整体平移无法发现 → 每页错位一格、第 1 张图正文永久丢失 | 出现 `<1` 的页码即整段判不可信（返回 None 回退）；`<<<END>>>` 对每一页截断 |
| A5 | 中 | 缓存只比对模型名 → "调大 `VISION_MAX_TOKENS` 修截断"后重试仍命中旧转录 | 指纹扩为 `model`/`provider`/`max_tokens`/`protocol` 四项 |
| B4 | 中 | JSON 回退协议也走内联 → 无去重、无 `---[NEXT]---` 边界 | 内联改由 `seams` 决定（只有每一批都拿到 NEW/CONT 才内联） |
| B5 | 中 | 求解失败时已付费的转录不落库（历史搜索查不到） | `pipeline._backfill_cached_transcript()` 在失败/取消/重解路径回填**仅空列** |
| A6 | 低 | `_provider_sampling_params` 按"extra_body 是否为空"分支（`VISION_DISABLE_THINKING=false` 时误发静默失效的 temperature/top_p） | 改按 provider 能力位分支 |
| A7 | 低 | 缺密钥时 `_call_vision_api(stream=True)` 返回 None → 收集器抛 `TypeError` 被记成"模型错误"，掩盖真因 | 返回与"重试耗尽"一致的错误标记，调用方直接回退 |
| A9 | 低 | `refill_pages` 串行、无上限（8 页最坏 ≈70 分钟） | 复用 `OCR_PARALLEL_WORKERS` 并行补页；补页前补齐页列表（修越界隐患 A15） |
| B6 | 低 | OCR 归档从不清理（删任务后题面仍留在盘上） | `delete_ocr_archives()` + 删任务/保留策略两处接线，且带"绝不越出 `OCR_DIR`"护栏 |
| B7 | 低 | 视觉阶段没跑到就取消的任务被记成 `vision_mode="parallel"` | 预置值改为空串（"未走到"） |
| B8 | 低 | 列表/搜索返回完整 OCR 文本（百条任务可达数 MB） | 列表/搜索用列投影剔除两个重列并保持键存在；详情接口仍返回全文 |
| B9 | 低 | A/B 工具的兜底单价表仍是迁移前的（低估 4 倍） | 改为与生产 `COST_TABLE` 一致的兜底表 + 一致性用例 |
| B10–B14 | NIT | `ended` 未透出、死常量、注解错误、前端 `StageTimings` 缺 `filename`、求解层硬编码 `provider == "deepseek"` | 全部修复（`ended` 进结果契约；`config.provider_supports_thinking_control()` 两处共用） |

### 11.6 已知未修项 / 后续建议（诚实清单）

| # | 项 | 影响 | 为什么暂时不做 |
|---|---|---|---|
| 1 | ~~`webapp/app._startup_cleanup()` 清理孤儿上传目录时**不**清对应 OCR 归档~~ | — | **已修（2026-09-22）**：`_startup_cleanup` 在 `prune_uploads` 之后对同一批孤儿 id 调 `delete_ocr_archives(orphans)`；护栏与上传同源（空任务库走告警分支提前返回，删除还带 `OCR_DIR` 包含性校验）。回归用例 `test_startup_cleanup_removes_ocr_archives_of_orphan_uploads` / `test_startup_cleanup_keeps_ocr_archives_when_task_db_is_empty` |
| 2 | `webapp` 的缓存读取只校验 `model`/`provider`，不校验 `max_tokens`/`protocol` | 用旧 `VISION_MAX_TOKENS` 写下的转录仍可被 `/resolve` 复用（那是"当时付过钱的文本"，语义上可接受） | 与 core 的严格指纹是**有意的不对称**：resolve 的目标是"别再付一次钱"，core 的目标是"别复用可能被截断的结果" |
| 3 | 空页（模型对纯图页合法返回空块）也会进 `failed_pages` 并触发一次补做 | 每张纯图页浪费 1 次单页调用（≈1–3 s） | 无法与"模型其实没读出来"区分；宁可多补一次，不可漏页 |
| 4 | `timeout=300` 是 httpx 的**单次操作**超时，不是整段墙钟上限 | 慢速滴流的响应可能远超 300 s | 需要把 deadline 传进 `_collect_stream`；当前无实测触发案例 |
| 5 | ~~`stage="ocr"` 的用量事件用 `pages × calls` 反映输入 token~~ | — | **已修（2026-09-22）**：`UsageReport` 新增 `images` 字段（**实际上传的图片张数**，与 `calls` 解耦），`webapp/usage.py` 的 `estimate_tokens(pages, chars, *, calls, images)` 把"固定开销×调用次数 + 图片×每图 token"分开算。旧口径把 OCR 的 8 张图按 8 次调用乘成 64 张，8 图回退路径高估输入 token **4.5 倍**；合并路径不受影响（`calls` 恒为 1）。纯文本阶段（润色/求解/重解/文件名）显式上报 `images=0`，否则会被兜底成 `calls × pages`。老 payload（无 `images` 键）按旧口径兜底，不会少扣。回归用例：`tests/test_usage.py`（14 条） |
| 6 | `compare_pages` 的 docstring 仍说基准是"两版并集"（代码只用基准页） | 文档不准确 | 改成并集语义会**削弱** B2 的空基准修复 |
| 7 | ~~真机 A/B（S1/S2）~~ | — | **已完成（2026-09-21）**：`ZHIPU_API_KEY` 补回后跑完 5 组 40 张图的要素比对 + 36 个逐图分类样本，判定 S1/S2 通过（§8.4） |
| 8 | ~~8 图"分批 vs 并行 vs 一次带完"的耗时受服务端负载影响大~~ | — | **已解决（2026-09-22）**：改用**交替轮次 + 配对比较**（§8.7），得到并行 4/4 cycle 全胜（3.71 s vs 6.01 s），并用服务端 `usage` 实测出并行成本是分批的近 2 倍（17155 vs 8792 prompt token）。默认值维持 `VISION_BATCH_SIZE=4`，依据改为"耗时 / 成本 / 补页率"三维度 |

**下一步（按优先级）**：
1. ~~填回 `ZHIPU_API_KEY` → 跑 A/B → 把 S1/S2 判定回填 §8.4~~ → **已完成 2026-09-21**（§8.4）；
2. ~~S1/S2 通过 → 把 `config.DEFAULT_VISION_PROVIDER` 由 `zhipu` 改为 `deepseek`~~ → **已完成 2026-09-21**（一行默认值 + 全部文档措辞同步）；
3. ~~多时段复测 `VISION_BATCH_SIZE` 的默认值~~ → **已完成 2026-09-22**（§8.7：交替轮次 + 真实 token 成本）；
4. ~~处理 11.6 第 1、5 两项~~ → **两项均已完成 2026-09-22**（无主任务 OCR 归档清理；图片 token 计费口径）。

> **迁移文档至此无未决项、无阻塞项。** 唯一剩下的第 6 条是"docstring 措辞不准"，
> 且**故意不改**（改成并集语义会削弱空基准的假 PASS 修复，见该行说明）。

**已拍板项（2026-09-22，用户确认）**：§8.7 显示并行回退路径快约 2.3 s、贵约 60%，
**决定保持成本优先的分批合并**（`VISION_BATCH_SIZE=4`）。若将来改为延迟优先，
`USE_COMBINED_VISION_CALL=false` 一行即可切到并行（token 代价见 §8.7 实测表）。


