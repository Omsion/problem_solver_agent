import type {
  AdminDashboard,
  AdminUserDetail,
  AdminUserList,
  AuthUser,
  MeResponse,
  SendCodeResponse,
  Task,
  TaskDetail,
  TokenResponse,
  UserRole,
} from "../types";
import { getAuthHeaders, getToken } from "./auth";

const BASE = "/api";

/** `/api/v1` 下的认证与管理员接口前缀（既有任务接口仍在 `/api` 下） */
const V1 = "/api/v1";

/** 统一的 API 错误，携带状态码与后端错误码，便于 UI 区分处理 */
export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/**
 * 把响应体解析成 `ApiError`。
 *
 * 项目里并存三种错误体：
 * - `{"error": "..."}`             —— 早期任务接口
 * - `{"error": {"code","message"}}`—— 带错误码的旧格式
 * - `{"detail": {"code","message"}}`—— FastAPI `HTTPException` 的对象 detail
 *   （认证与管理员接口全部使用这种形式，`detail` 也可能是普通字符串）
 * 三者都要给出可读的 message，否则界面只能显示兜底文案。
 */
async function parseError(res: Response, fallback: string): Promise<ApiError> {
  let message = fallback;
  let code: string | undefined;
  try {
    const body = await res.json();
    if (typeof body?.error === "string") {
      message = body.error;
    } else if (body?.error?.message) {
      message = body.error.message;
      code = body.error.code;
    } else if (typeof body?.detail === "string") {
      message = body.detail;
    } else if (body?.detail?.message) {
      message = body.detail.message;
      code = body.detail.code;
    }
  } catch {
    /* 保留 fallback */
  }
  return new ApiError(message, res.status, code);
}

/**
 * 统一请求：自动附加 `Authorization`，网络层异常归一成 ApiError。
 *
 * `json` 只有传了才带 `Content-Type`，避免让 GET 也声明 JSON body。
 */
async function request<T>(
  path: string,
  options: { method?: string; json?: unknown; networkMessage?: string; fallback?: string } = {},
): Promise<T> {
  const { method = "GET", json, networkMessage = "网络连接失败，请检查网络后重试", fallback = "请求失败" } = options;
  const headers: Record<string, string> = { ...getAuthHeaders() };
  if (json !== undefined) headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: json === undefined ? undefined : JSON.stringify(json),
    });
  } catch {
    throw new ApiError(networkMessage, 0, "network_error");
  }
  if (!res.ok) throw await parseError(res, `${fallback}（${res.status}）`);
  // 204 等空响应没有 JSON 体
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/** 上传图片并创建任务 */
export async function createTask(files: File[]): Promise<{ task_id: string; num_images: number }> {
  const form = new FormData();
  for (const f of files) {
    form.append("files", f);
  }
  let res: Response;
  try {
    // FormData 让浏览器自己带 boundary，因此这里只能手写请求而不能走 request()
    res = await fetch(`${BASE}/tasks`, { method: "POST", headers: getAuthHeaders(), body: form });
  } catch {
    throw new ApiError("网络连接失败，请检查手机与电脑是否在同一局域网", 0, "network_error");
  }
  if (!res.ok) throw await parseError(res, `上传失败（${res.status}）`);
  return res.json();
}

/** 获取单个任务详情（含解答内容与图片 URL） */
export async function getTask(taskId: string): Promise<TaskDetail> {
  return request<TaskDetail>(`${BASE}/tasks/${encodeURIComponent(taskId)}`, {
    networkMessage: "网络连接失败，请检查网络后重试",
    fallback: "任务不存在",
  });
}

/** 任务列表 */
export async function listTasks(limit = 100): Promise<{ tasks: Task[] }> {
  return request<{ tasks: Task[] }>(`${BASE}/tasks?limit=${limit}`, {
    networkMessage: "网络连接失败，请检查网络后重试",
    fallback: "加载任务列表失败",
  });
}

/** 删除任务 */
export async function deleteTask(taskId: string): Promise<void> {
  await request<unknown>(`${BASE}/tasks/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
    fallback: "删除失败",
  });
}

/** 取消正在处理的任务 */
export async function cancelTask(taskId: string): Promise<void> {
  await request<unknown>(`${BASE}/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: "POST",
    fallback: "取消失败",
  });
}

/** 重试失败或已取消的任务（后端会复用阶段缓存） */
export async function retryTask(taskId: string): Promise<void> {
  await request<unknown>(`${BASE}/tasks/${encodeURIComponent(taskId)}/retry`, {
    method: "POST",
    fallback: "重试失败",
  });
}

/** 换路重解：复用已识别的题目文本，只重跑求解（可换风格 / 开关思考模式） */
export async function resolveTask(
  taskId: string,
  options: { thinking?: boolean; style?: "OPTIMAL" | "EXPLORATORY" } = {},
): Promise<{ status: string; style: string; thinking: boolean }> {
  const params = new URLSearchParams();
  if (options.thinking !== undefined) params.set("thinking", options.thinking ? "1" : "0");
  if (options.style) params.set("style", options.style);
  const qs = params.toString();
  return request(`${BASE}/tasks/${encodeURIComponent(taskId)}/resolve${qs ? "?" + qs : ""}`, {
    method: "POST",
    fallback: "重新求解失败",
  });
}

/** 核对答案：用第二个视觉模型对照原图复核（默认关闭的可选功能） */
export async function verifyTask(
  taskId: string,
): Promise<{ status: string; verification: import("../types").VerificationResult }> {
  return request(`${BASE}/tasks/${encodeURIComponent(taskId)}/verify`, {
    method: "POST",
    fallback: "核对失败",
  });
}

/**
 * 给 SSE 地址附加令牌。
 *
 * 为什么不用请求头：`EventSource` 不支持自定义请求头，而任务进度依赖 SSE。
 * 只支持请求头的话，开启登录后流式端点会直接 401。后端同时接受
 * `?token=` 与 `?api_key=`（见 webapp/deps.py，其中说明了安全代价）。
 */
function withStreamAuth(url: string): string {
  const token = getToken();
  if (!token) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(token)}`;
}

/** 构造任务 SSE 地址（自动附带登录令牌） */
export function sseUrl(taskId: string, thinking = false): string {
  const params = new URLSearchParams();
  if (thinking) params.set("thinking", "1");
  const qs = params.toString();
  return withStreamAuth(`${BASE}/tasks/${encodeURIComponent(taskId)}/stream${qs ? "?" + qs : ""}`);
}

/** 构造全局 SSE 地址（自动附带登录令牌） */
export function globalSseUrl(): string {
  return withStreamAuth(`${BASE}/events/stream`);
}

/** 系统状态（自动导入是否在跑、监控目录等） */
export async function getSystemStatus(): Promise<import("../types").SystemStatus> {
  return request(`${BASE}/status`, { fallback: "获取系统状态失败" });
}

/** 后端健康与配置探测（不触发任何外部 API 调用） */
export async function getHealth(): Promise<import("../types").HealthInfo> {
  return request(`${BASE}/health`, { fallback: "获取后端状态失败" });
}

/** 阶段耗时与缓存命中统计 */
export async function getStats(limit = 50): Promise<import("../types").StatsResponse> {
  return request(`${BASE}/stats?limit=${limit}`, { fallback: "获取统计失败" });
}

// ---- 认证（/api/v1/auth）----

/** 请求短信验证码。开发模式（SMS_PROVIDER=console）下响应会带 `debug_code` */
export async function sendCode(phone: string): Promise<SendCodeResponse> {
  return request<SendCodeResponse>(`${V1}/auth/send-code`, {
    method: "POST",
    json: { phone },
    fallback: "发送验证码失败",
  });
}

/** 注册并直接拿到登录令牌。`password` 留空时后端用手机号后 6 位作为初始密码 */
export async function register(payload: {
  phone: string;
  code: string;
  password?: string;
}): Promise<TokenResponse> {
  return request<TokenResponse>(`${V1}/auth/register`, {
    method: "POST",
    json: payload,
    fallback: "注册失败",
  });
}

/** 手机号 + 密码登录 */
export async function login(payload: { phone: string; password: string }): Promise<TokenResponse> {
  return request<TokenResponse>(`${V1}/auth/login`, {
    method: "POST",
    json: payload,
    fallback: "登录失败",
  });
}

/** 当前用户 + 用量 + `auth_enabled` 开关 */
export async function getMe(): Promise<MeResponse> {
  return request<MeResponse>(`${V1}/auth/me`, { fallback: "获取用户信息失败" });
}

/** 重新生成 API Key；完整 key 仅此一次返回 */
export async function rotateApiKey(): Promise<{ api_key: string; api_key_masked: string }> {
  return request(`${V1}/auth/api-key/rotate`, { method: "POST", fallback: "生成 API Key 失败" });
}

// ---- 管理员（/api/v1/admin）----

/** 管理员看板总览 */
export async function getAdminDashboard(): Promise<AdminDashboard> {
  return request<AdminDashboard>(`${V1}/admin/dashboard`, { fallback: "获取看板数据失败" });
}

/** 管理员用户列表（分页） */
export async function getAdminUsers(skip = 0, limit = 50): Promise<AdminUserList> {
  return request<AdminUserList>(`${V1}/admin/users?skip=${skip}&limit=${limit}`, {
    fallback: "获取用户列表失败",
  });
}

/** 管理员查看单个用户详情（含用量流水） */
export async function getAdminUser(userId: string): Promise<AdminUserDetail> {
  return request<AdminUserDetail>(`${V1}/admin/users/${encodeURIComponent(userId)}`, {
    fallback: "获取用户详情失败",
  });
}

/** 管理员调整用户额度（元） */
export async function updateUserBudget(userId: string, budget: number): Promise<{ user: AuthUser }> {
  return request(`${V1}/admin/users/${encodeURIComponent(userId)}/budget`, {
    method: "PATCH",
    json: { budget },
    fallback: "调整额度失败",
  });
}

/** 管理员调整用户角色 */
export async function updateUserRole(userId: string, role: UserRole): Promise<{ user: AuthUser }> {
  return request(`${V1}/admin/users/${encodeURIComponent(userId)}/role`, {
    method: "PATCH",
    json: { role },
    fallback: "调整角色失败",
  });
}
