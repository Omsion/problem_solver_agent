import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** 合并 Tailwind class，后者覆盖前者的冲突项 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/**
 * 把 Unix 时间戳（秒）格式化为本地时间字符串。
 *
 * 缺陷 D 的教训：旧实现在遇到 NaN / undefined 时会抛
 * `RangeError: Invalid time value`，React 会卸载整棵子树，
 * 手机端表现为"一片空白"。这里对非法输入返回占位符而不是抛错。
 */
export function formatTs(ts: number | null | undefined): string {
  if (ts === null || ts === undefined || !Number.isFinite(ts) || ts <= 0) {
    return "--";
  }
  const date = new Date(ts * 1000);
  if (Number.isNaN(date.getTime())) return "--";
  try {
    return date.toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return date.toISOString();
  }
}

/** 把毫秒耗时格式化为易读文本：820ms / 4.2s / 1m12s */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) return "--";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m${rest}s`;
}

/** 生成上传文件的本地唯一 id */
export function uid(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}
