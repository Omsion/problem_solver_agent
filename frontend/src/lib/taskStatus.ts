import type { TaskStatus } from "../types";

/** 任务是否已进入终态（不会再变化） */
export function isTerminalStatus(status: TaskStatus | string | undefined): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}

/** 是否需要（或正在）实时流式推送 */
export function isActiveStatus(status: TaskStatus | string | undefined): boolean {
  return status === "pending" || status === "processing";
}

const STATUS_LABELS: Record<string, string> = {
  pending: "等待中",
  processing: "处理中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export function statusLabel(status: string | undefined): string {
  if (!status) return "未知";
  return STATUS_LABELS[status] ?? status;
}
