import type { Task, TaskDetail } from "../types";

const BASE = "/api";

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

async function parseError(res: Response, fallback: string): Promise<ApiError> {
  let message = fallback;
  let code: string | undefined;
  try {
    const body = await res.json();
    // 兼容两种错误体：{"error": "..."} 与 {"error": {"code","message"}}
    if (typeof body?.error === "string") {
      message = body.error;
    } else if (body?.error?.message) {
      message = body.error.message;
      code = body.error.code;
    } else if (body?.detail) {
      message = typeof body.detail === "string" ? body.detail : fallback;
    }
  } catch {
    /* 保留 fallback */
  }
  return new ApiError(message, res.status, code);
}

/** 上传图片并创建任务 */
export async function createTask(files: File[]): Promise<{ task_id: string; num_images: number }> {
  const form = new FormData();
  for (const f of files) {
    form.append("files", f);
  }
  let res: Response;
  try {
    res = await fetch(`${BASE}/tasks`, { method: "POST", body: form });
  } catch {
    throw new ApiError("网络连接失败，请检查手机与电脑是否在同一局域网", 0, "network_error");
  }
  if (!res.ok) throw await parseError(res, `上传失败（${res.status}）`);
  return res.json();
}

/** 获取单个任务详情（含解答内容与图片 URL） */
export async function getTask(taskId: string): Promise<TaskDetail> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/tasks/${encodeURIComponent(taskId)}`);
  } catch {
    throw new ApiError("网络连接失败，请检查网络后重试", 0, "network_error");
  }
  if (!res.ok) throw await parseError(res, `任务不存在（${res.status}）`);
  return res.json();
}

/** 任务列表 */
export async function listTasks(limit = 100): Promise<{ tasks: Task[] }> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/tasks?limit=${limit}`);
  } catch {
    throw new ApiError("网络连接失败，请检查网络后重试", 0, "network_error");
  }
  if (!res.ok) throw await parseError(res, "加载任务列表失败");
  return res.json();
}

/** 删除任务 */
export async function deleteTask(taskId: string): Promise<void> {
  const res = await fetch(`${BASE}/tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" });
  if (!res.ok) throw await parseError(res, "删除失败");
}

/** 取消正在处理的任务 */
export async function cancelTask(taskId: string): Promise<void> {
  const res = await fetch(`${BASE}/tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST" });
  if (!res.ok) throw await parseError(res, "取消失败");
}

/** 重试失败或已取消的任务（后端会复用阶段缓存） */
export async function retryTask(taskId: string): Promise<void> {
  const res = await fetch(`${BASE}/tasks/${encodeURIComponent(taskId)}/retry`, { method: "POST" });
  if (!res.ok) throw await parseError(res, "重试失败");
}

/** 构造任务 SSE 地址 */
export function sseUrl(taskId: string, thinking = false): string {
  const params = new URLSearchParams();
  if (thinking) params.set("thinking", "1");
  const qs = params.toString();
  return `${BASE}/tasks/${encodeURIComponent(taskId)}/stream${qs ? "?" + qs : ""}`;
}

/** 构造全局 SSE 地址 */
export function globalSseUrl(): string {
  return `${BASE}/events/stream`;
}

/** 系统状态（自动导入是否在跑、监控目录等） */
export async function getSystemStatus(): Promise<import("../types").SystemStatus> {
  const res = await fetch(`${BASE}/status`);
  if (!res.ok) throw await parseError(res, "获取系统状态失败");
  return res.json();
}

/** 后端健康与配置探测（不触发任何外部 API 调用） */
export async function getHealth(): Promise<import("../types").HealthInfo> {
  const res = await fetch(`${BASE}/health`);
  if (!res.ok) throw await parseError(res, "获取后端状态失败");
  return res.json();
}

/** 阶段耗时与缓存命中统计 */
export async function getStats(limit = 50): Promise<import("../types").StatsResponse> {
  const res = await fetch(`${BASE}/stats?limit=${limit}`);
  if (!res.ok) throw await parseError(res, "获取统计失败");
  return res.json();
}
