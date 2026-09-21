# TODO.md — 后续改进计划

> **本文档定位**：可执行的改进清单，不是需求文档。
> 需求与商业目标见同目录 `new_plan.md`（原始商业计划，1425 行）。
> 架构与决策见 `../ARCHITECTURE.md`；接口契约见 `../API.md`；部署见 `../DEPLOY.md`。

**建立时间**：第二轮商业化改造结束后
**当前基线**（改动前请先复验）：

```powershell
pytest                                    # 274 passed
cd frontend; npx tsc -b; npx eslint . --max-warnings=0; npx vitest run   # 117 passed
```

---

## 一、已修正的计划错误（**不要重复引入**）

原始商业计划里有三处依赖判断有误。若后续要接入这些能力，请按此表处理，不要照抄原计划代码。

### 1.1 `qwbp` 是无关包

| 项 | 内容 |
|---|---|
| 原计划 | `npm install qwbp`，用 `new QWBP()` / `createOffer()` / `acceptOffer()` 做零服务器 WebRTC 信令 |
| 事实 | npm 上 `qwbp@0.1.0` 确实存在，但**与本项目场景无关**，不提供上述 API。照抄必然报错 |
| 结论 | 该"零服务器扫码交换 SDP"方案需要自己实现（约 200 行：二维码编码 SDP → 扫码解码 → 回传 answer）。**未实现** |

### 1.2 `jsQR` 拼写错误

| 项 | 内容 |
|---|---|
| 原计划 | `import jsQR from "jsqr"` —— 包名与导入名不一致 |
| 事实 | npm 包名是 **`jsqr`**（全小写），但该包的导出并非默认导出 `jsQR` 函数；真实可用的库是 **`jsqr`** 与 `qr-scanner`、`@zxing/browser` 等 |
| 结论 | 若要做扫码解析，先 `npm view` 核实导出形式再写。当前**前端未实现摄像头扫码** |

### 1.3 `@authhero/admin` 不能直接用

| 项 | 内容 |
|---|---|
| 原计划 | `npm install @authhero/admin ra-core ra-data-simple-rest`，当作 react-admin 发行版使用 |
| 事实 | 该包是 **AuthHero 认证产品的专用管理 UI**（其依赖里确实有 `ra-core@^5.14.7`，因为它是 react-admin 应用），许可证 **AGPL-3.0-only**。要用它就得把认证也换成 AuthHero |
| 已采取的替代 | 后端 `/api/v1/admin/*` 的列表接口按 react-admin 的 `{data, total}` 约定返回，前端用现有技术栈自建看板（`frontend/src/pages/AdminPage.tsx`）。**将来若真要换 react-admin，后端无需改动** |

### 1.4 LiteLLM + Postgres 的预算体系未采用

| 项 | 内容 |
|---|---|
| 原计划 | 用 LiteLLM 虚拟密钥 + Postgres 做预算/限流/消费追踪 |
| 冲突 | 需额外运行 Postgres 与 LiteLLM 两个服务，与"本地部署、开箱可用"矛盾 |
| 已采取的替代 | 自研 `webapp/accounts.py`：`users.api_key` 等价虚拟密钥，`budget/spent` 等价预算，`usage_events` 等价消费流水。**零外部依赖** |
| 何时该换成 LiteLLM | 需要多副本横向扩容、或需要按真实 token 精确计费时（见 §四） |

---

## 二、已修复的缺陷（**补测试时优先覆盖，防止回归**）

| 缺陷 | 根因 | 修复位置 | 回归用例 |
|---|---|---|---|
| SSE 终态事件投递不到订阅者，客户端干等 30 秒心跳 | `TaskEventBus.cleanup()` 连**订阅队列**一起删，事件发布时已无接收者 | `webapp/routes.py`：`cleanup()` 只清历史与序号，不再删队列；`_run` 结束前用 `_terminal_fallback()` 兜底补发终态事件 | `tests/test_multitenancy.py::test_usage_event_is_recorded` |
| 开启登录后任务流 401，核心功能失效 | 浏览器 `EventSource` **无法自定义请求头** | `webapp/deps.py` 增加 `?token=` / `?api_key=` 查询参数；前端 `frontend/src/lib/api.ts` 的 `withStreamAuth()` 附加令牌 | `tests/test_auth.py`（依赖注入部分）+ `frontend/src/lib/api.error.test.ts` |
| 超额后才报错，已经花掉一次付费调用 | 只在上传后校验 | `webapp/routes.py`：`create_task` 落盘前、`resolve_task` 求解前调 `_check_budget()`，不足返回 402 | `tests/test_multitenancy.py::test_submit_is_rejected_when_budget_exhausted` |
| 全套测试从 18 秒涨到 58 秒 | scrypt `n=2**14` 单次 45ms，测试反复建用户 | `webapp/accounts.py`：`_is_test_process()` 判断 pytest 进程后降到 `n=2**12`（**不影响线上强度**） | 无（性能回归，靠观察套件耗时） |
| 远程连接误判导致"手机扫码"按钮自己消失 | IP 黑名单把本机请求判成手机 | `problem_solver_agent/netcheck.py` 改为本机地址白名单 + 移动端 UA | `tests/test_netcheck.py` |
| 历史记录快速切换后页面不显示 | `loadingTaskRef` 早退守卫跳过整段加载 | `frontend/src/App.tsx` 改幂等加载 + `#/task/:id` | `frontend/src/components/output/AnswerCard.test.tsx` 等 |

---

## 三、已定位但**尚未修复**的问题（建议优先处理）

### P1 — `record_usage` 会写孤儿流水，污染全局口径

- **位置**：`webapp/accounts.py` 的 `record_usage()`
- **根因**：先 `INSERT usage_events`，再 `UPDATE users SET spent = spent + ?`；当 `user_id` 不存在时后者匹配 0 行静默 no-op，但流水已落库
- **复现**：
  ```python
  m.record_usage(user_id="u_ghost", stage="solve", model="deepseek-flash", output_tokens=1_000_000)
  m.global_usage_summary()   # {'calls': 1, 'cost': 2.0, 'top_users': []}  ← 无法归属的花费
  ```
- **触发路径**：`webapp/usage.py:80` 只做 `if not user_id` 真值判断；用户被删除后任务完成即可触发
- **建议修法**：`record_usage` 开头校验用户存在，不存在则 `logger.warning` 并返回 `None`；或改为 `UPDATE ... WHERE id = ?` 后用 `cursor.rowcount == 0` 判定并回滚事务
- **验收**：新增用例断言"对不存在用户记账不产生流水，且全局汇总不受影响"

### P2 — `verify_sms_code` 无失败次数限制，可被爆破

- **位置**：`webapp/accounts.py` 的 `verify_sms_code()`
- **根因**：同一手机号在 TTL（默认 300 秒）内可无限次尝试 6 位数字码，失败不计数、不作废
- **建议修法**：`sms_codes` 表加 `attempts INTEGER DEFAULT 0`；失败自增，达到 N（建议 5）次即删除该行并记录日志；同时给 `/api/v1/auth/send-code` 加按手机号的发送频率限制（例如 60 秒一次，持久化到表里）
- **验收**：新增用例断言"同一手机号第 6 次校验失败后，即使验证码正确也不再通过"

### P3 — `withStreamAuth` 不判令牌过期，与 `getAuthHeaders` 行为不一致

- **位置**：`frontend/src/lib/api.ts` 的 `withStreamAuth()`
- **根因**：直接取 `getToken()` 原值；而 `frontend/src/lib/auth.ts` 的 `getAuthHeaders()` 会清掉过期令牌
- **影响**：带过期令牌发起首次 SSE 必然 401 → 进入重连循环，而不是干净的"请重新登录"
- **建议修法**：`withStreamAuth` 复用与 `getAuthHeaders` 相同的过期判断，过期则不带 token（让 401 走正常登出流程）
- **注意**：`frontend/src/lib/api.error.test.ts` 已把**当前行为**固定为断言，修改时需同步更新该用例

### P4 — 管理员"调用数"列数据有损

- **位置**：`frontend/src/pages/AdminPage.tsx`
- **根因**：`User.to_public_dict()` 不含 `calls`，页面只能从 `/api/v1/admin/dashboard` 的 `top_users`（前 20）反查
- **建议修法**：`AccountManager.list_users()` 增加 LEFT JOIN 聚合 `calls`/`cost` 字段，或新增 `/api/v1/admin/users?with_usage=1`
- **验收**：管理员用户列表里每个用户的调用数与 `usage_events` 实际条数一致

### P5 — `RequireAdmin` 与 `RequireAuth` 的失败策略相反

- **位置**：`frontend/src/components/auth/AuthRoutes.tsx`
- `RequireAuth` 在 `authEnabled === null && !isLoading`（探测失败）时 **fail-open 放行**；`RequireAdmin` 同条件下 **fail-closed 显示"需要管理员权限"**
- **判断**：方向上是安全的一侧，**可以不改**；若要统一，建议统一为 fail-closed 并给明确提示文案

### P6 — `verify_task` 的 `model` 参数未使用

- **位置**：`webapp/routes.py` 的 `verify_task(model: str | None = None)`
- 参数被接收但未透传给 `pipeline_service.verify()`，因此无法用指定模型核对。要么透传，要么从签名里删掉避免误导

---

## 四、明确延期的计划阶段（**不要在本轮硬塞**）

以下都来自 `new_plan.md`，但各有硬前置条件。**理由不是"太难"，而是塞进来会得到一个跑不起来的半成品**。

| 计划阶段 | 前置条件 | 触发条件（何时值得做） |
|---|---|---|
| 阶段一：Tauri 2 桌面端 | Rust 工具链；要删除 `tools/silent_screencapper.py`、`tools/remote_trigger.py` | 需要免装 Python 的单文件分发、或系统级热键/托盘常驻时 |
| 阶段二：WebRTC 5G 传图 | 信令通道（自研二维码交换 或 云服务器）；TURN 兜底 | 手机不在同一局域网，需要异地传图时。**验收标准：手机拍照后 2 秒内 PC 出现图片** |
| 阶段三：LiteLLM 网关 | Postgres + LiteLLM 两个服务；`litellm/config.yaml` | 需要多模型 fallback/负载均衡，或按真实 token 精确计费时 |
| 阶段零/四：Postgres + `fastapi-rls` | Postgres；`pip install "fastapi-rls[all]"`（**已核实为真实包**，`RLSMixin`/`TenantPolicy`/`RLS` 均存在，注意 0.1.0 为 beta） | 需要多副本横向扩容，或要数据库层面的租户隔离时才值得。当前用 `tasks.user_id` 在应用层隔离已足够 |
| 阶段四：Alembic 迁移 | — | **建议优先于 Postgres 迁移**：现在 `webapp/models.py` 的 `_migrate_tasks()` 是手写幂等迁移，字段一多就难维护 |
| 阶段四：`@authhero/admin` | 需把认证换成 AuthHero | **不建议**。已自建看板且后端接口保持 react-admin 兼容，换的成本远大于收益 |
| 阶段七：`docker-compose` 全栈 | 已交付单服务版 | 若接入 Postgres/Redis，再按 `new_plan.md` 的多服务编排扩展 |

---

## 五、建议的下一步任务清单

按"收益/成本"排序。每项都给了落点文件与验收标准，可直接作为独立需求开工。

### 任务 A：修掉三个已知安全/一致性问题（约半天）

- A1 → §三 P1（孤儿流水）
- A2 → §三 P2（验证码限流）
- A3 → §三 P3（SSE 令牌过期判断）
- **验收**：三项各有新增用例；`pytest` 与 `npx vitest run` 全绿

### 任务 B：用量口径从"估算"升级为"精确"（约一天）

- **现状**：`webapp/usage.py` 按"页数 × 1024 + 输出字符 × 0.6"估算 token
  （2026-09-20 视觉层迁移把每图折算从 700 提到 1024 —— DeepSeek 官方给出的每图 token
  上限，保守取值）；视觉 / 分类 / OCR 阶段 `output_chars` 记 0。迁移后新增了两个阶段名
  `vision_refill`（单页补做）与 `filename`（仅当本地生成失败才调模型），用量流水里按
  `stage` 可区分
- **目标**：非流式调用读取 `response.usage` 的精确值；流式调用开启 `stream_options={"include_usage": True}`（需确认所接网关是否支持，不支持则回退估算并在流水里标记 `estimated: true`）
- **落点**：`problem_solver_agent/solver_client.py`（`stream_solve` 目前丢弃 usage 字段）、`problem_solver_agent/vision_client.py`、`webapp/usage.py`、`webapp/accounts.py`（`usage_events` 加 `estimated` 列）
- **验收**：新增用例断言"网关返回 usage 时记账取精确值且 `estimated=false`；未返回时 `estimated=true` 且不崩"

### 任务 C：把"用量/额度"接入实际调用链的完整闭环（约一天）

- **现状**：`create_task`/`resolve_task` 有预检，`retry_task` 没有；管理员完全不受额度限制（`_check_budget` 里跳过）
- **决策点**：需要你确认「管理员是否应受额度限制」。当前行为是为兼容 `AUTH_ENABLED=false`（内置本地用户是 admin + 1e9 额度）而设计的
- **落点**：`webapp/routes.py` 的 `retry_task`、`_check_budget`
- **验收**：`retry` 在零额度时返回 402；管理员策略按你的选择固定下来并有对应用例

### 任务 D：真正的端到端集成测试（约一天）

- **现状**：`pytest` 全部打桩，不调用真实 API；SSE 的 30 秒问题就是靠临时脚本才定位到的
- **建议**：新增 `tests/integration/`，用 `TestClient` + **桩流水线**覆盖完整链路（上传 → 隔离 → 额度 → SSE 事件序列 → 用量落库 → 管理看板数字一致），**不打桩的部分是 HTTP 与数据库层**
- **注意**：`TestClient` 对 `StreamingResponse` 会缓冲到流结束才交给 `iter_text()`（保活心跳 30 秒），因此**不要断言流式延迟**，只断言事件序列与落库结果；这一点已在 `test_usage_event_is_recorded` 的 docstring 里记录
- **验收**：链路级用例 ≥ 8 个，且整套 `pytest` 保持在 60 秒内

### 任务 E：前端视觉与组件持续收口（约两天）

- **现状**：已引入 radix-ui / sonner / lucide-react / @tanstack/react-query，但仍有手写原语（`ui/button.tsx`、`ui/card.tsx`、`ui/badge.tsx`）与原生 `navigator.clipboard` 降级路径
- **建议**：把剩余原语迁到 Radix 语义；`frontend/src/components/output/OutputPanel.tsx` 的 `handleCopyAll` 与 `AnswerCard` 的 `copy` 都有 `document.execCommand` 兜底，可行的话抽成一个 `useCopy()` 复用
- **验收**：`npm run build` 首屏 JS 不显著增长（当前 450 KB / gzip 141 KB）；`eslint` 全绿

### 任务 F（可选，需你决策）：异地传图

- **现状**：手机与电脑必须同一局域网（扫码用 `_get_lan_ip()` 生成的地址）
- **选项**：
  1. 自研二维码交换 SDP 的 WebRTC（零服务器，需两次扫码）
  2. 买最低配云服务器跑 WebSocket 信令（约 40–50 元/月）
  3. 不做（继续局域网）
- **验收标准**（来自原计划）：手机拍照后 2 秒内 PC 端 `Screenshots/` 出现图片

---

## 六、每次改动后的固定验证流程

```powershell
# 1. 后端（应 274+ passed，60 秒内）
pytest

# 2. 前端类型 / 检查 / 单测
cd frontend
npx tsc -b --pretty false
npx eslint . --max-warnings=0
npx vitest run

# 3. 生产构建（改了 frontend/src 就必须做，否则 8000 端口仍是旧产物）
npm run build

# 4. 一键自检（路径 / 密钥 / 服务可达 / 图片压缩收益）
cd ..
python tools/diag.py

# 5. 多人模式冒烟（可选，验证登录链路）
$env:AUTH_ENABLED="true"; $env:AUTH_SECRET_KEY="dev-secret"; python run_web.py 8126
```

---

## 七、当前能力的边界（避免误期待）

| 能力 | 现状 |
|---|---|
| 部署形态 | 单进程（SQLite + 内存事件总线），**不支持 `uvicorn --workers > 1`** |
| 异步模型 | 线程池 + 信号量，**未上全异步**（OpenAI 同步 SDK 的阻塞调用无法真正取消） |
| SSE 续传 | 每任务保留最近 500 条事件用于 `Last-Event-ID` 回放，超出后重头收 |
| 计费 | **估算**，非精确账单；单价表在 `webapp/accounts.py` 的 `COST_TABLE` |
| 验证码 | `SMS_PROVIDER=console` 时回显在响应里（仅开发）；**未接入真实短信服务** |
| 核对模式 | 需要原图未被清理，否则返回 `upload_expired` |
| 前端测试 | 只有逻辑与组件行为测试，**无视觉回归测试** |
| 截图工具 | `tools/silent_screencapper.py` 需管理员权限；`tools/remote_trigger.py` 仍用 Flask（与主 FastAPI 技术栈并存） |

---

## 八、维护本文档的约定

- **做了什么就删掉对应条目**，不要让已完成项堆积
- 发现新的计划错误（像 §一 那样）**追加到 §一**，注明"原计划怎么写、事实是什么、怎么改"
- 修掉的缺陷移到 §二 并补上回归用例路径
- §四 的"触发条件"是**判断依据**：条件不成立就不要提前动手
