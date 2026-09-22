# API 契约

前端与后端的唯一依据。前端类型定义见 `frontend/src/types/index.ts`，改动任一侧
请同步另一侧。

Base URL：`http://<host>:8000`，所有接口以 `/api` 开头。

错误响应有三套形态，取决于接口归属（**改动前端时都要处理**）：

1. 任务类接口（`/api/tasks*`）返回 `{"error": ...}`，`error` 可以是字符串或对象：

```json
{"error": "人类可读的中文说明", "code": "machine_readable_code"}
```

`code` 并非每个错误都有（例如"任务不存在"只有 `error` 字符串）；额度不足时
`error` 是对象，详见下文「计费与额度」。

2. 认证 / 管理员接口（`/api/v1/*`）走 FastAPI `HTTPException(detail={...})`，
因此实际响应体是 `{"detail": {...}}`：

```json
{"detail": {"code": "unauthorized", "message": "请在请求头中提供 Bearer 令牌或 X-API-Key"}}
```

3. FastAPI 自身的参数校验失败（`422`，例如 `limit` 越界）返回
`{"detail": [{...}]}`——注意 `detail` 是**数组**，不属于上面两种对象形态。

前端 `ApiError`（`frontend/src/lib/api.ts`）会解析 `{"error": "..."}`、
`{"error": {"code","message"}}`、`{"detail": "..."}` 与 `{"detail": {"code","message"}}`
四种形态；`422` 的数组形态匹配不到，会退化为调用处传入的兜底文案。

---

## REST 接口

> 本节所有 `/api/tasks*`、`/api/status`、`/api/stats` 端点都要求身份
> （`AUTH_ENABLED=true` 时需 Bearer 令牌或 `X-API-Key`，详见下文「访问控制总览」）。
> 对**不属于当前用户**的任务，各端点一律返回与"任务不存在"完全相同的 `404`，
> 刻意不区分，避免把 task_id 变成可枚举探测的信息。

### `POST /api/tasks`

上传图片并创建任务。

- Content-Type：`multipart/form-data`
- 字段：`files`（可重复，至少一张）

响应 `200`：

```json
{"task_id": "20260616-103429-293e", "num_images": 4}
```

错误：

| 状态码 | code | 场景 |
|---|---|---|
| 400 | — | 未上传图片 / 扩展名不支持 / 文件名无效（这三类只返回 `error` 文本，无 `code`） |
| 400 | `invalid_image` | 内容不是有效图片（Pillow 无法解码） |
| 402 | `insufficient_budget` | 额度预检不通过，见「计费与额度」（`error` 为对象，带 `remaining` / `required`） |
| 413 | `too_large` | 超出 `MAX_UPLOAD_SIZE`（MB） |

额度预检放在读取上传内容之前：余额不足时在落盘前就拒绝，不会留下半个任务目录。

安全说明：文件名只取 basename 并清理非法字符，同名自动加序号；
内容用 Pillow 校验可解码，不信任扩展名。

### `GET /api/tasks`

任务列表，按创建时间倒序。

- Query：
  - `limit`（默认 100，源码未设上限）
  - `q`（可选）：关键词搜索。传入且非空时改走 `TaskManager.search_tasks`，对
    `problem_text` / `ocr_raw_text` / `filename` 三列做 `LIKE '%q%'`（`%` `_` 会被转义，
    不会退化成通配符）；过滤与排序语义与不带 `q` 时完全一致（仍是创建时间倒序 + `limit`）。
    **未上 FTS5** —— 任务表按 `TASK_RETENTION_COUNT`（默认 100）截断，百行表上 LIKE 是
    微秒级；FTS5 默认分词器对中文无效，必须 `tokenize='trigram'`，还要多维护一张虚表与
    同步逻辑，收益为零。等表涨到万级再换。
- 身份：普通用户只返回自己的任务；管理员（含 `AUTH_ENABLED=false` 的内置本地用户）返回全部

```json
{"tasks": [{
  "id": "...", "status": "completed", "problem_type": "MULTIPLE_CHOICE",
  "solver_provider": "deepseek", "solver_model": "deepseek-flash",
  "solution_path": "...", "filename": "4-7_技术选择题综合解答.md",
  "problem_text": "（送入求解的题目文本）", "ocr_raw_text": "（逐页原始 OCR 拼接）",
  "vision_mode": "batched",
  "error_message": "", "num_images": 4,
  "created_at": 1781577269.86, "updated_at": 1781577417.56,
  "timings": {"classify": 1800, "ocr": 2400, "polish": 0, "solve": 38200, "total": 42400, "cached": []}
}]}
```

`status` 取值：`pending` / `processing` / `completed` / `failed` / `cancelled`。

任务对象的完整字段（`TaskManager._row_to_task`，库中 `timings_json` 会改名为 `timings`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 任务 id，`YYYYMMDD-HHMMSS-<4 位十六进制>` |
| `status` | string | 见上 |
| `created_at` / `updated_at` | number | Unix 秒 |
| `num_images` | int | 图片数 |
| `problem_type` | string | 分类结果，未分类为空串 |
| `solver_provider` / `solver_model` | string | 实际使用的求解器 |
| `solution_path` | string | 解答 Markdown 的绝对路径 |
| `filename` | string | 归档文件名 |
| `problem_text` | string | 送入求解的题目文本（润色后 / 合并内联拼接后）；历史任务未落库时为空串 |
| `ocr_raw_text` | string | 逐页原始 OCR 拼接（**未被润色改写**，便于命中被改写掉的关键词）；为空串表示未落库 |
| `vision_mode` | string | 视觉路径：`combined`（PAGE 分隔符合并调用）/ `json`（JSON 回退协议）/ `parallel`（分类 + 并行 OCR 回退）；空串按 `parallel` 理解 |
| `error_message` | string | 失败原因，成功时为空串 |
| `answer_card` | string | 答案卡**正文**（`result["answer_card"]["text"]`，纯文本，非 JSON），无则为空串 |
| `verified` | int | `1` 表示已执行过核对 |
| `user_id` | string | 归属用户（多用户隔离的依据） |
| `tenant_id` | string | 归属租户 |
| `timings` | object \| null | 阶段耗时；从未记录过为 `null` |

> `answer_card` 与 `verified` 是数据库原始形态（`answer_card` 只存答案卡正文、
> `verified` 为 0/1），与 SSE `done` 事件里的 `answer_card` **对象**不同：
> 后者是 `{"text", "extracted", "section", "truncated"}`（见 `problem_solver_agent/answer_card.py`）。

### `GET /api/tasks/{task_id}`

任务详情，含解答正文与图片地址。

```json
{
  "task": { "...同上..." },
  "solution_content": "# 解答\n\n...",
  "image_urls": ["/uploads/<task_id>/1.jpg", "..."],
  "answer_card": {"text": "选 C。", "extracted": true, "section": "最终答案", "truncated": false}
}
```

`solution_content` 仅在任务状态为 `completed` 且 `solution_path` 存在时非空（`cancelled`
任务的部分内容也返回空串，需要查看 `.partial.md` 文件）；文件不存在时返回空串。
`image_urls` 直接列出上传目录里的所有文件。

`answer_card` 与 SSE `done` 事件里的答案卡**同源**（都由 `answer_card.py` 抽取），
字段含义见上文「用量与计价」一节旁的答案卡说明：

- 有解答文件时，从文件内容现场抽取（会先剥掉 YAML frontmatter 与「题目文本」小节，
  并按 LF 归一化换行——Windows 写的文件是 CRLF，不归一化会让小节正则全部失配，
  卡片会退化成元信息/题面）；
- 文件已被清理、但库里有落库的卡片文本时，用它兜底；
- 两者都没有时为 `null`。

前端拿到它就不要再在整篇文件上"猜"答案，否则 `problem_type`、`solver`、题面都会被
当成最终答案显示出来。

| 状态码 | code | 场景 |
|---|---|---|
| 404 | — | 任务不存在，或不属于当前用户（只有 `error` 文本，无 `code`） |

### `DELETE /api/tasks/{task_id}`

删除任务，同时删除解答文件、上传目录与阶段缓存。

```json
{"status": "ok"}
```

| 状态码 | code | 场景 |
|---|---|---|
| 404 | — | 任务不存在，或不属于当前用户（只有 `error` 文本，无 `code`） |

普通用户只能删自己的任务；他人任务与"不存在"返回同一个 404，不暴露存在性。

### `POST /api/tasks/{task_id}/cancel`

取消处理中的任务。`DELETE` 方法保留作为兼容别名。

行为：置位取消令牌 → 立即把状态改为 `cancelled` → 推送 `cancelled` 事件。
流水线会在下一个阶段边界或流式分片之间停止，已生成内容保存为
`<task_id>.partial.md`。

```json
{"status": "ok", "signalled": true, "message": "取消请求已发送"}
```

| 状态码 | code | 场景 |
|---|---|---|
| 400 | `not_cancellable` | 任务已处于终态 |
| 404 | — | 任务不存在 |

### `POST /api/tasks/{task_id}/retry`

重试失败或已取消的任务。会复用 `stage_cache` 中的分类/识别结果，
因此不会重复消耗视觉模型额度。

- Query：`thinking`（默认 true）

```json
{"status": "ok", "task_id": "...", "resumed": true, "thinking": true}
```

| 状态码 | code | 场景 |
|---|---|---|
| 400 | `not_retryable` | 只有 `failed` / `cancelled` 可重试 |
| 409 | `already_running` | 该任务正在处理中 |
| 410 | `upload_expired` | 原图已被清理，需要重新截图/上传 |

调用成功后，前端应重新连接 `GET /api/tasks/{id}/stream`。

### `POST /api/tasks/{task_id}/resolve`

**换路重解**：复用已识别的题目文本，只重跑求解，跳过分类与 OCR。

适用场景：第一版答案不满意，想换求解风格、开关思考模式，或换个模型再要一版。
因为跳过视觉步骤，整个过程只有一次求解调用。

- Query：
  - `thinking`：`1` / `0`，默认 `1`
  - `style`：`OPTIMAL` / `EXPLORATORY`，留空用全局配置

```json
{"status": "ok", "task_id": "...", "style": "EXPLORATORY", "thinking": true, "reused_transcript": true}
```

题目文本的来源顺序：解答文件里的「题目文本」小节 → `stage_cache` 中的识别结果。

| 状态码 | code | 场景 |
|---|---|---|
| 400 | — | `style` 取值非法 |
| 402 | `insufficient_budget` | 额度预检不通过（`error` 为对象，带 `remaining` / `required`） |
| 404 | — | 任务不存在（含不属于当前用户） |
| 409 | `already_running` | 任务正在处理中 |
| 409 | `no_transcript` | 找不到已识别的题目文本，需先完整处理一次 |
| 410 | `upload_expired` | 图形推理题依赖原图，但原图已被清理 |

成功后同样需要连接 `stream` 端点接收新一版解答（`done` 事件会带 `resolved: true`）。

### `POST /api/tasks/{task_id}/verify`

**核对模式**（可选功能，默认不启用）：用视觉推理模型对照原图复核答案。

不覆盖已有解答，核对结果会追加到解答文件末尾的「## 核对结果」小节。

- Query：`model`（可选）覆盖默认的视觉推理模型；默认取自当前视觉 provider 的
  `VISION_REASONING_MODEL`（`deepseek` → `deepseek-flash`，`zhipu` → `GLM-4.6V`）

```json
{
  "status": "ok",
  "task_id": "...",
  "verification": {
    "verdict": "disagree",
    "issues": ["第 2 小问漏答", "选项 B 与题干要求矛盾"],
    "corrections": "应选 A，并补上第二问的推导",
    "reason": "",
    "model": "deepseek-flash"
  }
}
```

`verdict` 取值：

| 值 | 含义 |
|---|---|
| `agree` | 核对通过 |
| `disagree` | 发现明确错误（`issues` 非空） |
| `unclear` | 无法判定（图片信息不足 / 模型输出无法解析 / 调用失败） |

后端有一层**保守保护**：模型声称 `disagree` 但没有给出任何问题或修正建议时，
会降级为 `unclear` 并说明原因，避免误报把正确答案吓成"错误"。

| 状态码 | code | 场景 |
|---|---|---|
| 400 | `not_verifiable` | 只有 `completed` / `cancelled` 的任务可核对 |
| 404 | — | 任务不存在 |
| 409 | `empty_answer` | 解答内容为空 |
| 410 | `upload_expired` | 原图已被清理，无法对照核对 |
| 500 | — | 核对过程异常 |

### `GET /api/tasks/{task_id}/stream`

SSE 流式端点。**首次连接会启动流水线**（幂等：已在运行则只订阅）。

- Query：
  - `thinking`：`1` 启用求解器思考模式（DeepSeek reasoning），默认关闭。
    关闭时求解器会**显式**下发 `thinking.type=disabled`——实测只把该参数省略掉
    模型照样思考，所以"关掉思考"必须显式声明
  - `style`：编程题风格 `OPTIMAL` / `EXPLORATORY`，留空用全局配置

思考模式下**思考过程与正文共享 `SOLVER_MAX_TOKENS`（默认 16000）**：思考把配额吃满时
模型会以 `finish_reason=length` 收尾且正文为空。此时求解器会自动改为关闭思考模式重试一次
（前端会先收到一条说明用的 `reasoning` 事件），两次都拿不到正文才报错，且错误信息会带上
模型名、`finish_reason` 与思考/正文字符数。

引擎侧还有一层**按需升级**（`core_pipeline._solve_with_escalation`）：

1. 先按首选档跑一次。首选档默认**不开思考**，由 `SOLVER_THINKING_DEFAULT` 决定；
   `thinking=1` 显式指定时直接走思考档，跳过升级逻辑。
2. 只在答案**不合格**时才升级到「开思考 + `SOLVER_ESCALATE_MAX_TOKENS`（默认 32000）」
   重跑：正文为空、`finish_reason=length`（被截断）、或编程题（ACM/LeetCode/ML_CODING）
   答案短于 `SOLVER_ESCALATE_MIN_CHARS`（默认 500）。选择题这类答案天然很短的题型不会触发。
3. 升级档同样没写出正文时**沿用第一版**，不做第三次调用。
4. 每次求解都会打一条「求解画像」日志（模型 / 是否思考 / effort / 配额 / 思考字符 /
   正文字符 / finish_reason），用于事后判断该不该继续开思考。

「不合格」只针对**答案是否完整**，不判断对错——判对错请用「核对答案」。

终态任务不会重新启动流水线，而是立即返回单个对应事件。

### `GET /api/events/stream`

全局 SSE：广播 `auto_imported`、`remote_connected`、`remote_disconnected`。

- 身份：**免鉴权**（与 `GET /api/health`、`GET /api/qrcode` 同）；连接建立后会先发一条
  `event: init` / `{"type": "init", "message": "connected"}`，随后每 30 秒发一次
  `: heartbeat` 注释行保活

### `GET /api/status`

运行状态。用于「设置」页展示监控是否在跑、磁盘占用等。

- 身份：需要（`AUTH_ENABLED=true` 时缺少/无效凭证返回 `401`）；磁盘占用与监控目录属于部署内部信息

```json
{
  "auto_import_enabled": true, "running": true,
  "monitor_dir": "D:\\Users\\wzw\\Pictures\\Screenshots",
  "group_timeout": 8.0, "started_at": 1781580000.0,
  "last_group_at": 1781580100.0, "groups_handled": 3, "processing": 0,
  "uploads_bytes": 47815065, "solutions_bytes": 1240000,
  "remote_connected": false, "lan_ip": "192.168.1.5"
}
```

### `GET /api/stats`

阶段耗时统计，回答「到底慢在哪」。

- Query：`limit`（默认 50，源码内被夹到 `1 ~ 200`）
- 身份：需要；普通用户只统计自己的任务（避免从聚合结果反推别人的题量与耗时），管理员统计全部

```json
{
  "sample_size": 12, "completed": 10, "failed": 2, "cache_hit_rate": 0.15,
  "stages": {
    "solve": {"p50": 38200.0, "p90": 61000.0, "average": 41000.0, "samples": 10, "cache_hits": 0}
  }
}
```

### `GET /api/health`

轻量健康检查，**不触发任何外部 API 调用**。用于探活与手机端连通性自检。

本接口与 `GET /api/qrcode`、`GET /api/events/stream` 是仅有的三个**免鉴权**端点
（源码中没有 `Depends(get_current_user)`），`AUTH_ENABLED=true` 时也能匿名访问。

```json
{
  "status": "ok", "version": "2.0.0",
  "vision_configured": true,
  "solver_providers": ["deepseek"],
  "keys_configured": {"deepseek": true}
}
```

### `GET /api/qrcode`

返回局域网访问二维码 PNG，URL 形如 `http://<lan_ip>:8000`。

### 静态挂载

| 路径 | 内容 |
|---|---|
| `/static/*` | 前端构建产物 |
| `/uploads/<task_id>/*` | 任务原图 |
| `/solutions/*` | 解答 Markdown |

未匹配的 `GET /api/*` 返回 `404` JSON（不会回落到 SPA 的 index.html）。

---

## 访问控制、账户、管理员与计费

本节对应源码：`webapp/config.py`、`webapp/deps.py`、`webapp/auth.py`、`webapp/accounts.py`、
`webapp/usage.py`、`webapp/routers/auth.py`、`webapp/routers/admin.py`。

### 访问控制总览

`AUTH_ENABLED`（`webapp/config.py:39`，默认 `false`）决定整套访问控制是否生效：

| 取值 | 模式 | 行为 |
|---|---|---|
| `false`（默认） | 单用户本地模式 | **不要求任何凭证**：所有请求都视为内置本地用户（`LOCAL_USER_ID`，默认 `local`）。该用户由 `AccountManager.ensure_local_user()` 创建，角色 `admin`、额度 `1e9` 元，任务可见性为"全部可见"。行为与改造前一致，便于单人自用与回归测试。 |
| `true` | 多用户模式 | 任务、状态、统计与管理员接口都要求身份；普通用户只能看到/操作自己的任务，他人任务统一按 `404` 处理（刻意不用 `403`，避免把 task_id 变成可枚举探测的信息）；提交任务前做额度预检。 |

**相关环境变量**（`.env.example` 有逐项注释）：

| 变量 | 默认值 | 作用 |
|---|---|---|
| `AUTH_ENABLED` | `false` | 访问控制总开关 |
| `AUTH_SECRET_KEY` | 空（进程内临时密钥） | JWT 签名密钥，`AUTH_ENABLED=true` 时应显式设置 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | 令牌有效期（分钟） |
| `LOCAL_USER_ID` / `LOCAL_TENANT_ID` | `local` / `default` | 单用户模式的内置身份 |
| `DEFAULT_USER_BUDGET` | `10.0` | 新用户注册赠送额度（元） |
| `MIN_TASK_BUDGET` | `0.05` | 提交任务所需的最低剩余额度（元） |
| `SMS_PROVIDER` | `console` | 短信通道；`console` 时验证码回显在响应里 |
| `SMS_CODE_TTL_SECONDS` | `300` | 验证码有效期（秒） |
| `SMS_CODE_LENGTH` | `6` | 验证码位数 |
| `ADMIN_PHONES` | 空 | 逗号分隔的管理员手机号白名单 |

**身份来源与优先级**（`webapp/deps.py` 的 `get_current_user`，按顺序尝试）：

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1 | `Authorization: Bearer <JWT>` | 网页登录后签发的令牌；缺失、非法、过期或账号已删 → `401 unauthorized` |
| 2 | `X-API-Key: <key>` | 用户的开放 API 密钥（注册时自动生成，可用 `POST /api/v1/auth/api-key/rotate` 轮换）；无效 → `401 unauthorized` |
| 3 | 内置本地用户 | **仅当 `AUTH_ENABLED=false`**；此时忽略前两者，且不做额度校验 |

`AUTH_ENABLED=true` 且两者都没有 → `401 unauthorized`，响应头带 `WWW-Authenticate: Bearer`。
管理员接口在当前用户 `role != "admin"` 时 → `403 forbidden`。

**免鉴权端点**（源码中没有 `Depends(get_current_user)`，共三个）：

| 端点 | 说明 |
|---|---|
| `GET /api/health` | 探活 |
| `GET /api/qrcode` | 局域网二维码 |
| `GET /api/events/stream` | 全局 SSE 广播 |

其余接口（`/api/tasks*`、`/api/status`、`/api/stats`、`/api/v1/auth/me`、
`/api/v1/auth/api-key/rotate`、`/api/v1/admin/*`）在 `AUTH_ENABLED=true` 时都要求身份。

**JWT 细节**（`webapp/auth.py`）：HS256，标准库 `hmac` + `base64` 自实现，payload 固定为：

| claim | 含义 |
|---|---|
| `sub` | 用户 id（如 `u_1a2b3c4d5e6f7a8b`），鉴权时用它查库取最新用户 |
| `role` | `user` / `admin`（**签发时的快照，鉴权不信任它**，每次都按库里的角色判定） |
| `tenant_id` | 租户 id（注册时固定为 `LOCAL_TENANT_ID`，默认 `default`） |
| `iat` / `exp` | 签发时间 / 过期时间（秒级时间戳） |

`exp = iat + ACCESS_TOKEN_EXPIRE_MINUTES * 60`（默认 1440 分钟 = 24 小时）。
`alg` 只接受 `HS256`（显式拒绝 `none`，避免算法混淆）。未配置 `AUTH_SECRET_KEY` 时
进程内生成临时密钥：**服务重启后已签发的令牌全部失效**，生产必须显式配置。

### 认证接口

前缀 `/api/v1/auth`（`webapp/routers/auth.py:26`）。手机号格式为 `^1[3-9]\d{9}$`
（中国大陆手机号，`auth.py:29`），不匹配一律 `400 invalid_phone`。

除特别说明外，本组接口的错误体是
`{"detail": {"code": "...", "message": "..."}}`（FastAPI 对象 `detail`）。

#### `POST /api/v1/auth/send-code`

发送短信验证码，验证码存库并设 TTL（`accounts.save_sms_code`）。

请求体：

```json
{"phone": "13800000000"}
```

`200`（`SMS_PROVIDER=console`，**默认**；此时不真正发短信，验证码直接回显在
`debug_code` 里——仅供本地开发与自测）：

```json
{"ok": true, "provider": "console", "expires_in": 300, "debug_code": "123456"}
```

`200`（其它 provider 尚未接入，明确告知而不是静默失败）：

```json
{
  "ok": false, "provider": "aliyun", "expires_in": 300,
  "message": "SMS_PROVIDER=aliyun 尚未接入，请使用 console 或实现对应发送逻辑"
}
```

| 状态码 | code | 场景 |
|---|---|---|
| 400 | `invalid_phone` | 手机号格式不正确 |

验证码长度 `SMS_CODE_LENGTH`（默认 6 位数字），有效期 `SMS_CODE_TTL_SECONDS`
（默认 300 秒）；校验成功后立即删除，是一次性的（`accounts.py:462`）。

#### `POST /api/v1/auth/register`

注册并**直接返回登录令牌**。

请求体：

```json
{"phone": "13800000000", "code": "123456", "password": "可选；留空则用手机号后 6 位"}
```

`password` 不传或留空时，后端使用手机号后 6 位作为初始密码
（`payload.password or phone[-6:]`，`auth.py:120`）——这是**弱口令**，正式上线前
应要求用户在首次登录后改密。

`200`：

```json
{
  "access_token": "<JWT，HS256，claims 见上表>",
  "token_type": "bearer",
  "expires_in_minutes": 1440,
  "user": {
    "id": "u_1a2b3c4d5e6f7a8b", "phone": "138****0000", "role": "user",
    "tenant_id": "default", "budget": 10.0, "spent": 0.0, "remaining": 10.0,
    "api_key_masked": "sk-solv...xxxx",
    "created_at": 1781577269.86, "last_login_at": null
  }
}
```

> `last_login_at` 是**本次请求之前**的值：`_issue_token()` 先 `touch_login()` 写库，
> 但返回的是更早取出的 `User` 对象，所以注册响应里它是 `null`，登录响应里它是**上一次**
> 的登录时间（`webapp/routers/auth.py:57-65`）。

注册副作用：

- 赠送 `DEFAULT_USER_BUDGET` 额度（默认 `10.0` 元）
- 手机号命中 `ADMIN_PHONES`（逗号分隔的环境变量）时 `role` 为 `admin`，否则为 `user`
- 自动生成一把 API Key（`sk-solver-<token_urlsafe(32)>`），接口只回掩码

| 状态码 | code | 场景 |
|---|---|---|
| 400 | `invalid_phone` | 手机号格式不正确 |
| 400 | `phone_taken` | 手机号已注册 |
| 400 | `bad_code` | 验证码不正确或已过期 |

#### `POST /api/v1/auth/login`

请求体：

```json
{"phone": "13800000000", "password": "123456"}
```

成功响应与 `register` **完全一致**（同一个 `_issue_token`，含 `access_token` /
`token_type` / `expires_in_minutes` / `user`）。

| 状态码 | code | 场景 |
|---|---|---|
| 401 | `bad_credentials` | 手机号或密码错误 |

- `login` **不校验手机号格式**（源码只做 `strip()`）：格式非法的手机号同样得到
  `bad_credentials`，不会返回 `invalid_phone`。
- 后端刻意不区分"用户不存在"与"密码错误"，避免枚举手机号。
- 密码哈希用标准库 scrypt，格式 `scrypt$<salt>$<hash>`。

#### `GET /api/v1/auth/me`

无请求体。`200`：

```json
{
  "user": { "...同 register 响应里的 user..." },
  "usage": {
    "calls": 12, "input_tokens": 9600, "output_tokens": 24000, "cost": 0.0672,
    "by_model": [{"model": "deepseek-flash", "calls": 6, "cost": 0.0528}],
    "by_stage": [{"stage": "solve", "calls": 6, "cost": 0.0528}]
  },
  "auth_enabled": true
}
```

| 状态码 | code | 场景 |
|---|---|---|
| 401 | `unauthorized` | `AUTH_ENABLED=true` 且缺少/无效凭证 |

`auth_enabled` 就是 `config.AUTH_ENABLED`，前端据此决定是否显示登录页。
`AUTH_ENABLED=false` 时本接口同样可用（返回内置本地用户），此时 `user.phone` 与
`user.api_key_masked` 都是空串（本地用户没有手机号，也没有密钥）。

#### `POST /api/v1/auth/api-key/rotate`

重新生成 API Key（**旧 Key 立即失效**）。无请求体。

`200`：

```json
{
  "api_key": "sk-solver-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "api_key_masked": "sk-solv...xxxx"
}
```

`api_key` 是完整密钥，**仅此一次返回**，之后任何接口都只给 `api_key_masked`
（`mask_key()`：`key[:7] + "..." + key[-4:]`）。请立即保存。

| 状态码 | code | 场景 |
|---|---|---|
| 401 | `unauthorized` | 未提供有效身份 |
| 404 | `not_found` | 用户不存在 |

### 管理员接口

前缀 `/api/v1/admin`（`webapp/routers/admin.py:22`）。要求当前用户 `role == "admin"`
（手机号命中 `ADMIN_PHONES`，或被另一个管理员通过 `PATCH .../role` 提升），否则：

| 状态码 | code | 场景 |
|---|---|---|
| 401 | `unauthorized` | 未提供有效身份 |
| 403 | `forbidden` | 已登录但不是管理员（`webapp/deps.py:85`） |

> `AUTH_ENABLED=false` 时内置本地用户就是 `admin`，因此这些接口在单用户模式下也能直接访问。

#### `GET /api/v1/admin/dashboard`

总览。`200`：

```json
{
  "total_users": 3,
  "calls": 42,
  "cost": 1.234567,
  "input_tokens": 123456,
  "output_tokens": 45678,
  "top_users": [
    {"id": "u_1a2b3c4d5e6f7a8b", "phone": "138****0000", "role": "user",
     "budget": 10.0, "spent": 1.2345, "cost": 1.234567, "calls": 12}
  ]
}
```

`calls` / `cost` / `input_tokens` / `output_tokens` / `top_users` 来自
`accounts.global_usage_summary()`（全表聚合），`total_users` 来自 `count_users()`。
`top_users` 是**按花费倒序的前 20 名用户**；因为用的是 `LEFT JOIN`，没有任何用量的
用户也会以 `cost: 0, calls: 0` 出现，`phone` 为掩码值。

#### `GET /api/v1/admin/users`

Query：

| 参数 | 类型 | 默认 | 约束 |
|---|---|---|---|
| `skip` | int | 0 | `>= 0` |
| `limit` | int | 50 | `1 ~ 200`（越界返回 `422`，FastAPI 校验） |

`200`（结构与 react-admin `simpleRestProvider` 约定一致）：

```json
{"data": [{ "...User 对象..." }], "total": 3}
```

按 `created_at` 倒序；`total` 是用户总数（不受分页影响）。

#### `GET /api/v1/admin/users/{user_id}`

用户详情 + 用量汇总 + 最近流水。`200`：

```json
{
  "user": { "...User 对象..." },
  "usage": { "...Usage 对象..." },
  "events": [{ "...UsageEvent 对象..." }]
}
```

`events` 为该用户最近 **50** 条流水（`list_usage(limit=50)`）。

| 状态码 | code | 场景 |
|---|---|---|
| 404 | `not_found` | 用户不存在 |

#### `PATCH /api/v1/admin/users/{user_id}/budget`

请求体：

```json
{"budget": 20.0}
```

`200`：

```json
{"user": { "...User 对象，budget / remaining 已更新..." }}
```

只修改总额度 `budget`，**不动** `spent`；`remaining = round(max(0, budget - spent), 6)`。

| 状态码 | code | 场景 |
|---|---|---|
| 400 | `bad_budget` | `budget < 0`（额度不能为负） |
| 404 | `not_found` | 用户不存在 |

#### `PATCH /api/v1/admin/users/{user_id}/role`

请求体：

```json
{"role": "admin"}
```

`role` 会先 `strip()` 再转小写，只接受 `user` / `admin`。`200`：

```json
{"user": { "...User 对象，role 已更新..." }}
```

| 状态码 | code | 场景 |
|---|---|---|
| 400 | `bad_role` | 角色不是 `user` / `admin` |
| 404 | `not_found` | 用户不存在 |

把角色降级为 `user` 后，其**旧令牌也无法再访问管理员接口**：`get_current_user`
每次都按 `sub` 查库取最新的 `User`，JWT 里的 `role` claim 不被信任。

#### `GET /api/v1/admin/usage`（源码已注册，一并记录）

Query：`limit`（int，默认 100，`1 ~ 500`）。

```json
{
  "summary": {"calls": 42, "cost": 1.234567, "input_tokens": 123456,
              "output_tokens": 45678, "top_users": []},
  "limit": 100
}
```

实现上只返回全局汇总（`global_usage_summary()`）并回显 `limit`，
**并不返回逐条流水**（逐条流水请用 `GET /api/v1/admin/users/{user_id}`）。

### 数据结构参考

以下字段全部对照源码逐字核对，没有额外字段。

#### User 对象（`User.to_public_dict()`，`webapp/accounts.py:105`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 用户 id，`u_` + 16 位十六进制随机串 |
| `phone` | string | **掩码**手机号，形如 `138****8000`（`mask_phone()`）；无手机号时为空串 |
| `role` | string | `user` / `admin` |
| `tenant_id` | string | 租户 id，注册时为 `LOCAL_TENANT_ID`（默认 `default`） |
| `budget` | number | 总预充额度（元），保留 4 位小数 |
| `spent` | number | 已消费（元），保留 4 位小数 |
| `remaining` | number | `round(max(0, budget - spent), 6)` |
| `api_key_masked` | string | 密钥掩码，形如 `sk-solv...xxxx`；无密钥时为空串 |
| `created_at` | number | 创建时间（Unix 秒，浮点） |
| `last_login_at` | number \| null | 最近登录时间；从未登录为 `null` |

该对象**不含密码哈希，也不含完整 API Key**。

#### Usage 对象（`AccountManager.usage_summary()`，`webapp/accounts.py:391`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `calls` | int | 调用次数（流水条数） |
| `input_tokens` | int | 估算输入 token 累计 |
| `output_tokens` | int | 估算输出 token 累计 |
| `cost` | number | 估算费用合计（元），保留 6 位小数 |
| `by_model` | array | 每项 `{"model", "calls", "cost"}`，按 `cost` 倒序 |
| `by_stage` | array | 每项 `{"stage", "calls", "cost"}`，按 `cost` 倒序 |

`stage` 取自 core 上报的阶段，实际取值只有
`vision`（合并的视觉调用）、`classify`、`ocr`（回退路径：1 次分类 + 每页 1 次转录）、
`polish`（只有真的调用了润色模型才计入）、`solve`、`resolve`
（见 `problem_solver_agent/core_pipeline.py:200 / 213 / 220 / 242 / 287 / 433`）；
记账层在 core 未上报阶段时记为 `unknown`。`model` 在 provider 未回传模型名时可能是空串。

#### UsageEvent 对象（`AccountManager.list_usage()`，`webapp/accounts.py:383`）

即 `usage_events` 表的原始行：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 流水 id，`e_` + 16 位十六进制随机串 |
| `user_id` | string | 归属用户 |
| `tenant_id` | string | 租户 |
| `task_id` | string \| null | 关联任务 id |
| `provider` | string | provider 名（可能为空串） |
| `model` | string | 模型名（可能为空串） |
| `stage` | string | 阶段，默认 `unknown` |
| `input_tokens` | int | 估算输入 token |
| `output_tokens` | int | 估算输出 token |
| `cost` | number | 该次估算费用（元），保留 6 位小数 |
| `created_at` | number | 记录时间（Unix 秒） |

#### AdminDashboard 对象（`GET /api/v1/admin/dashboard`）

= `{"total_users": int}` + `global_usage_summary()`（`webapp/accounts.py:419`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `total_users` | int | 用户总数 |
| `calls` | int | 全局调用次数 |
| `cost` | number | 全局估算费用（元），保留 6 位小数 |
| `input_tokens` | int | 全局估算输入 token |
| `output_tokens` | int | 全局估算输出 token |
| `top_users` | array | 最多 20 条，按 `cost` 倒序，见下表 |

`top_users[]`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 用户 id |
| `phone` | string | 掩码手机号 |
| `role` | string | `user` / `admin` |
| `budget` | number | 用户总额度（原始值，未做 4 位小数处理） |
| `spent` | number | 用户已消费（原始值） |
| `cost` | number | 该用户流水费用合计，保留 6 位小数 |
| `calls` | int | 该用户的流水条数 |

### 错误码表（认证 / 账户 / 管理员）

下表是源码中实际出现的 `detail.code`，**不要在前端硬编码表外的 code**。

| code | 状态码 | 出处 | 场景 |
|---|---|---|---|
| `unauthorized` | 401 | `webapp/deps.py:40` | 令牌缺失/非法/过期、账号不存在或已删除、API Key 无效 |
| `forbidden` | 403 | `webapp/deps.py:85` | 需要管理员权限 |
| `invalid_phone` | 400 | `webapp/routers/auth.py:52` | 手机号格式不正确 |
| `phone_taken` | 400 | `webapp/routers/auth.py:108` | 手机号已注册 |
| `bad_code` | 400 | `webapp/routers/auth.py:114` | 验证码不正确或已过期 |
| `bad_credentials` | 401 | `webapp/routers/auth.py:137` | 手机号或密码错误 |
| `not_found` | 404 | `webapp/routers/auth.py:163`、`webapp/routers/admin.py:74 / 93 / 112` | 用户不存在 |
| `bad_budget` | 400 | `webapp/routers/admin.py:91` | 额度不能为负 |
| `bad_role` | 400 | `webapp/routers/admin.py:110` | 角色只能是 user 或 admin |
| `insufficient_budget` | 402 | `webapp/routes.py:219` | 提交 / 换路重解前额度预检不通过 |

任务类接口已有的 code（各自端点表格里也出现过，此处汇总便于检索）：
`too_large`(413)、`invalid_image`(400)、`not_cancellable`(400)、`not_retryable`(400)、
`already_running`(409)、`upload_expired`(410)、`no_transcript`(409)、
`not_verifiable`(400)、`empty_answer`(409)。

### 计费与额度：按 token **估算**，不是精确账单

> 本节措辞按源码原意：费用是**估算**出来的，用途是**限制滥用**，不是给用户出账单。
> 精确用量与真实价格请以各模型平台后台为准。

- **费用是估算的。** `webapp/accounts.py` 顶部注释写明"费用用『按 token 估算』的方式
  计算……不依赖任何外部记账服务"；`webapp/usage.py` 也说明标准 OpenAI SDK 只有在流式
  响应结束后才能从 `usage` 字段拿到精确 token，且并非所有兼容网关都会返回该字段，
  因此用保守的启发式换算替代。

- **token 由"页数 + 输出字符数"启发式换算**（`webapp/usage.py:24-40`）：

  | 常量 | 值 | 含义 |
  |---|---|---|
  | `BASE_INPUT_TOKENS` | 600 | 题面文本本身的输入开销（prompt 模板 + 上下文） |
  | `TOKENS_PER_IMAGE` | 1024 | 一张图片在视觉模型中的固定折算。取 1024 是因为 DeepSeek 官方给出的**每图 token 上限**就是 1024（服务端会把图二次缩放到约 1300×1300 等效）；沿用旧值 700 会在换到 `deepseek-flash` 后低估近 1/3 的输入成本 |
  | `CHARS_PER_TOKEN_FACTOR` | 0.6 | 每字符折算的 token 数（CJK 偏 1.0、英文偏 0.25，取 0.6 作折中） |

  于是（`estimate_tokens`）：

  ```
  输入 token = 600 + 页数 × 1024    # 输入侧只计图片与固定开销，题目文本真实长度未回传
  输出 token = int(输出字符数 × 0.6)
  多次调用（如逐页 OCR）再按 calls 次数整体放大
  ```

  哪些阶段会上报 `output_chars`（`problem_solver_agent/core_pipeline.py`）：
  视觉 / 分类 / OCR 阶段只报 `pages`，输出字符数按 **0** 计；
  只有 `polish`（输出为润色后的题面）与 `solve` / `resolve`（输出为解答正文）
  会报 `output_chars`。

  **所以记账口径既不完整也不精确**：输入侧只计图片与固定开销（题目文本的真实长度
  没有回传），输出侧只覆盖 `polish` / `solve` / `resolve` 三个阶段。这里的数字只是
  **估算口径下的记账值**，与真实 token 数没有一一对应关系。与之相对，提交前的
  `estimate_task_cost()` 是刻意**高估**的（见下一条），两者方向不同，不要混用。

- **计价公式**：`estimate_cost(model, in, out)`（`accounts.py:48`）=
  `round(in / 1e6 × 输入单价 + out / 1e6 × 输出单价, 6)`，单位元；未列出的模型走
  `default` 单价。单价表在 `webapp/accounts.py:40` 的 `COST_TABLE`，**可自行调整**：

  | model | 输入（元 / 百万 token） | 输出（元 / 百万 token） |
  |---|---|---|
  | `deepseek-flash` | 2.0 | 8.0 |
  | `deepseek-v4-pro` | 9.0 | 27.0 |
  | `GLM-4.6V-FlashX` | 0.5 | 1.5 |
  | `GLM-4.6V` | 2.0 | 6.0 |
  | `default`（未列出的模型） | 2.0 | 8.0 |

  DeepSeek 的价格**按高峰时段统计口径取值**（宁可高估也不低估，额度系统的目的是限制滥用）：
  官方 `deepseek-flash` 高峰为 2 / 8，空闲时段是高峰的一半（1 / 4），缓存命中输入仅
  0.02–0.04。`deepseek-v4-pro` 高峰 9 / 27。`GLM-*` 两条只有在
  `VISION_PROVIDER=zhipu` 时才会用到，保留是为了回退后仍能算对账。

- **额度用尽后提交任务会被拒绝（HTTP 402）。** `_check_budget()`
  （`webapp/routes.py:195`）在 `POST /api/tasks`（落盘之前）与
  `POST /api/tasks/{id}/resolve`（启动流水线之前）做预检：

  ```
  预估费用 = estimate_task_cost(页数)          # usage.py:97，偏保守：宁可高估也不低估
           = 1 次视觉调用（按当前 provider 的 VISION_CLASSIFY_MODEL 计价，
             默认 deepseek-flash；输入侧只算图片）
           + 1 次求解调用（默认 deepseek-flash，输出按 4000 字符估算）
  required = max(预估费用, MIN_TASK_BUDGET)     # MIN_TASK_BUDGET 默认 0.05 元
  ```

  > 视觉那一条**跟随 provider** 取模型名（`core_config.VISION_CLASSIFY_MODEL`），不再是
  > 写死的 `GLM-4.6V-FlashX` —— 否则 `VISION_PROVIDER=zhipu` 时会按 DeepSeek 的单价预检。

  余额不足时返回 `402`，且响应体是**对象形态**的 `error`：

  ```json
  {
    "error": {
      "code": "insufficient_budget",
      "message": "额度不足：剩余 0.0123 元，本次至少需要 0.0520 元",
      "remaining": 0.0123,
      "required": 0.052
    }
  }
  ```

  两点例外：**管理员不受额度限制**（`AUTH_ENABLED=false` 时内置本地用户也是 admin，
  所以单用户模式不会被额度拦住）；`POST /api/tasks/{id}/retry` 复用阶段缓存，
  **不做额度预检**。

- **记账时机与容错**：core 上报的 `usage` 事件属于内部账目，**只落库、不下发给前端**
  （`webapp/routes.py:442-453`），因此 SSE 契约里没有 `usage` 事件；记账失败只写一条
  warning，**绝不影响解题**（`webapp/usage.py:90`）。
- 单价表是唯一计价来源，调价只需改 `COST_TABLE`；已写入 `usage_events` 的历史记录
  **不会追溯重算**。

> **隐私提示（不要误解）**：图片以 `data:image/jpeg;base64,...` 发送。**Base64 是编码不是加密**，
> 服务端解码后的第一步就是原始像素，模型看到的就是完整原图。换 provider（智谱 ↔ DeepSeek）
> 只是换了接收方，**暴露面不变**；唯一根治手段是本机跑视觉模型。EXIF 元数据（GPS/设备/时间）
> 在预处理阶段被丢弃，但那是元数据保护，图片内容一个像素都没少。

> ⚠️ **`AUTH_ENABLED=false` 时任何能访问该端口的人都能消耗你的 API 额度。**
> 需要额度控制与多用户隔离时，请在 `.env` 里设置 `AUTH_ENABLED=true`。

---

## SSE 事件

每条事件的 `data:` 行是一个 JSON 对象，含 `type` 字段。

| type | 字段 | 说明 |
|---|---|---|
| `init` | `task_id`, `num_images` | 连接建立后立即发送 |
| `status` | `phase`, `message` | 阶段切换。`phase` ∈ `classifying` / `solving` / `archiving`（`problem_solver_agent/core_pipeline.py:180 / 251 / 296`）与 `verifying`（`webapp/routes.py` 的核对端点）。**当前源码没有 `ocr` 阶段** |
| `reasoning` | `content` | 思考过程分片（仅在 `thinking=1` 时） |
| `chunk` | `content` | 解答正文分片 |
| `timings` | `timings` | 阶段耗时汇总（结束时发送） |
| `done` | `task_id`, `filename`, `answer_card`, `timings`, `resolved` | 处理成功。`resolved: true` 表示这是换路重解的结果 |
| `cancelled` | `task_id`, `message`, `partial_path` | 任务被取消，已保留部分内容。`partial_path` 仅在流水线结束时发出（可能为 `null`）；取消请求本身立即推送的那一条只有 `task_id` 与 `message`，没有 `partial_path` |
| `error` | `task_id`, `message` | 处理失败 |
| `verified` | `task_id`, `verification` | 核对完成（结构同上文的 `verification`） |
| `auto_imported` | `task_id`, `num_images`, `source` | 监控目录发现新截图组（仅全局流） |
| `remote_connected` | `client_ip` | 手机已连接（仅全局流） |
| `remote_disconnected` | `client_ip` | 手机已断开（仅全局流） |

### 事件序号与断线续传

每帧都带 SSE 标准 `id:` 字段（每个任务内自增）：

```
id: 12
event: chunk
data: {"type":"chunk","content":"..."}
```

浏览器重连 `EventSource` 时会自动带上 `Last-Event-ID` 请求头，服务端据此
**只补发该序号之后的事件**，避免重连后中间过程丢失或重复渲染。

实现细节：

- 每个任务保留最近 `TaskEventBus.REPLAY_LIMIT`（默认 500）条事件用于回放
- 序号在 `cleanup(task_id)` 时一并清空，因此重试/重解会从 1 重新开始
- 内部字段（`_id` / `_task_id`）不会下发给前端

服务端每 30 秒发送一次 `: heartbeat` 注释行保活。

**客户端要求**：

- 事件用 `event: <type>` 命名，因此必须为每种类型注册监听（不能只依赖 `onmessage`）。
- 收到 `done` / `error` / `cancelled` 后应关闭连接。
- 连接意外断开时应指数退避重连，并在页面恢复可见时立即重连。
  参考实现：`frontend/src/features/stream/taskStream.ts`。

---

## 与旧版本的差异

改造前后端契约变化（前端已同步）：

| 项 | 旧 | 新 |
|---|---|---|
| 取消 | `DELETE`，只改状态不真停 | `POST`（`DELETE` 兼容），真实停止 |
| 重试 | 无 | `POST /api/tasks/{id}/retry` |
| 换路重解 | 无 | `POST /api/tasks/{id}/resolve`（跳过 OCR） |
| 核对 | 无 | `POST /api/tasks/{id}/verify`（默认关闭） |
| 状态/统计/健康 | 无 | `GET /api/status`、`/api/stats`、`/api/health` |
| 答案卡 | 无 | `done` 事件带 `answer_card`，前端渲染成独立卡片 |
| SSE 续传 | 无 | 每帧带 `id:`，支持 `Last-Event-ID` 补发 |
| 终端页面 | `#/?task=<id>` | `#/task/<id>` |
| 「取消」事件 | 复用 `error` | 独立的 `cancelled` 事件与 `cancelled` 状态 |
| 远程判定 | 手写 IP 黑名单（会误判） | 本机地址白名单 + 移动端 UA |
| 认证 | 无（所有人都是同一个用户） | `AUTH_ENABLED` 开关 + `POST /api/v1/auth/send-code` / `register` / `login`、`GET /auth/me`、`POST /auth/api-key/rotate` |
| 身份来源 | 无 | `Authorization: Bearer <JWT>` → `X-API-Key` → 内置本地用户（仅 `AUTH_ENABLED=false`） |
| 任务接口鉴权 | 匿名可用 | `AUTH_ENABLED=true` 时要求身份；`/api/health`、`/api/qrcode`、`/api/events/stream` 仍免鉴权 |
| 任务归属 | 无隔离，所有人可见全部任务 | 任务带 `user_id` / `tenant_id`；普通用户只见自己的，他人任务一律 `404` |
| 额度 | 无 | 注册赠 `DEFAULT_USER_BUDGET`；提交 / 换路重解前预检，不足返回 `402 insufficient_budget` |
| 用量 | 无 | `usage_events` 流水累计；`GET /api/v1/auth/me` 返回汇总，`usage` 事件不下发 |
| 视觉层 provider | 写死智谱 GLM-4.6V 系列 | `VISION_PROVIDER`（`deepseek` 默认 / `zhipu` 一键回退），密钥与模型名由 provider 表派生 |
| 任务表搜索字段 | 无 | `tasks` 新增 `problem_text` / `ocr_raw_text` / `vision_mode` 三列（幂等迁移自动补列），`GET /api/tasks` 支持 `q` 关键词搜索（LIKE，未上 FTS5） |
| 管理员 | 无 | `ADMIN_PHONES` 命中即 admin；`GET /api/v1/admin/dashboard` / `users` / `users/{id}`、`PATCH .../budget` / `role`、`GET /api/v1/admin/usage` |
| 计费口径 | 无 | 按「页数 + 输出字符数」估算 token × `COST_TABLE` 单价，**属于估算而非精确账单** |
