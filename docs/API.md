# API 契约

前端与后端的唯一依据。前端类型定义见 `frontend/src/types/index.ts`，改动任一侧
请同步另一侧。

Base URL：`http://<host>:8000`，所有接口以 `/api` 开头。

错误响应统一格式（可能是简化形式）：

```json
{"error": "人类可读的中文说明", "code": "machine_readable_code"}
```

前端 `ApiError` 会同时解析 `{"error": "..."}` 与 `{"error": {"code","message"}}`
两种形态。

---

## REST 接口

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
| 400 | — | 未上传图片 / 扩展名不支持 / 内容不是有效图片 / 文件名无效 |
| 413 | `too_large` | 超出 `MAX_UPLOAD_SIZE`（MB） |

安全说明：文件名只取 basename 并清理非法字符，同名自动加序号；
内容用 Pillow 校验可解码，不信任扩展名。

### `GET /api/tasks`

任务列表，按创建时间倒序。

- Query：`limit`（默认 100）

```json
{"tasks": [{
  "id": "...", "status": "completed", "problem_type": "MULTIPLE_CHOICE",
  "solver_provider": "deepseek", "solver_model": "deepseek-v4-pro",
  "solution_path": "...", "filename": "4-7_技术选择题综合解答.md",
  "error_message": "", "num_images": 4,
  "created_at": 1781577269.86, "updated_at": 1781577417.56,
  "timings": {"classify": 1800, "ocr": 2400, "polish": 0, "solve": 38200, "total": 42400, "cached": []}
}]}
```

`status` 取值：`pending` / `processing` / `completed` / `failed` / `cancelled`。

### `GET /api/tasks/{task_id}`

任务详情，含解答正文与图片地址。

```json
{
  "task": { "...同上..." },
  "solution_content": "# 解答\n\n...",
  "image_urls": ["/uploads/<task_id>/1.jpg", "..."]
}
```

`solution_content` 仅在任务完成（或已取消且有部分内容）时非空；文件不存在时返回空串。

### `DELETE /api/tasks/{task_id}`

删除任务，同时删除解答文件、上传目录与阶段缓存。

```json
{"status": "ok"}
```

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

### `GET /api/tasks/{task_id}/stream`

SSE 流式端点。**首次连接会启动流水线**（幂等：已在运行则只订阅）。

- Query：
  - `thinking`：`1` 启用求解器思考模式（DeepSeek reasoning），默认关闭
  - `style`：编程题风格 `OPTIMAL` / `EXPLORATORY`，留空用全局配置

终态任务不会重新启动流水线，而是立即返回单个对应事件。

### `GET /api/events/stream`

全局 SSE：广播 `auto_imported`、`remote_connected`、`remote_disconnected`。

### `GET /api/status`

运行状态。用于「设置」页展示监控是否在跑、磁盘占用等。

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

- Query：`limit`（默认 50，上限 200）

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

## SSE 事件

每条事件的 `data:` 行是一个 JSON 对象，含 `type` 字段。

| type | 字段 | 说明 |
|---|---|---|
| `init` | `task_id`, `num_images` | 连接建立后立即发送 |
| `status` | `phase`, `message` | 阶段切换。`phase` ∈ `classifying` / `ocr` / `solving` / `verifying` / `archiving` |
| `reasoning` | `content` | 思考过程分片（仅在 `thinking=1` 时） |
| `chunk` | `content` | 解答正文分片 |
| `timings` | `timings` | 阶段耗时汇总（结束时发送） |
| `done` | `task_id`, `filename`, `answer_card`, `timings` | 处理成功 |
| `cancelled` | `task_id`, `message`, `partial_path` | 任务被取消，已保留部分内容 |
| `error` | `task_id`, `message` | 处理失败 |
| `auto_imported` | `task_id`, `num_images`, `source` | 监控目录发现新截图组（仅全局流） |
| `remote_connected` | `client_ip` | 手机已连接（仅全局流） |
| `remote_disconnected` | `client_ip` | 手机已断开（仅全局流） |

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
| 状态 | 无 | `GET /api/status` |
| 统计 | 无 | `GET /api/stats` |
| 健康 | 无 | `GET /api/health` |
| 终端页面 | `#/?task=<id>` | `#/task/<id>` |
| 「取消」事件 | 复用 `error` | 独立的 `cancelled` 事件与 `cancelled` 状态 |
| 远程判定 | 手写 IP 黑名单（会误判） | 本机地址白名单 + 移动端 UA |
