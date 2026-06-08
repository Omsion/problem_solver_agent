import type { Task } from "../types";

const BASE = "/api";

/** Upload images and create a new task. */
export async function createTask(files: File[]): Promise<{ task_id: string; num_images: number }> {
  const form = new FormData();
  for (const f of files) {
    form.append("files", f);
  }
  const res = await fetch(`${BASE}/tasks`, { method: "POST", body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Upload failed (${res.status})`);
  }
  return res.json();
}

/** Fetch a single task with solution content and image URLs. */
export async function getTask(taskId: string): Promise<{ task: Task; solution_content: string; image_urls: string[] }> {
  const res = await fetch(`${BASE}/tasks/${taskId}`);
  if (!res.ok) throw new Error(`Task not found (${res.status})`);
  return res.json();
}

/** List recent tasks. */
export async function listTasks(limit = 100): Promise<{ tasks: Task[] }> {
  const res = await fetch(`${BASE}/tasks?limit=${limit}`);
  if (!res.ok) throw new Error(`List failed (${res.status})`);
  return res.json();
}

/** Delete a task. */
export async function deleteTask(taskId: string): Promise<void> {
  const res = await fetch(`${BASE}/tasks/${taskId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Delete failed (${res.status})`);
}

/** Construct SSE URL for streaming task progress. */
export function sseUrl(taskId: string, thinking = false): string {
  const params = new URLSearchParams();
  if (thinking) params.set("thinking", "1");
  const qs = params.toString();
  return `${BASE}/tasks/${taskId}/stream${qs ? "?" + qs : ""}`;
}
