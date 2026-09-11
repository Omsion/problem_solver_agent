/** 流式缓冲上限：长任务的思考过程可能达到数百 KB，超出后截断避免内存膨胀 */
export const MAX_BUFFER_CHARS = 200_000;
export const TRUNCATED_SUFFIX = "\n\n…（内容过长已截断）";

/**
 * 追加流式分片，超过上限后截断并标记一次。
 *
 * 注意两点：
 * 1. 截断后的总长度（含标记）不超过 limit，否则"上限"就名不副实了
 * 2. 一旦截断就不再继续追加，避免每来一个分片都做一次字符串拼接
 *    （长任务下这是主要的 CPU 开销来源）
 */
export function appendBounded(prev: string, next: string, limit = MAX_BUFFER_CHARS): string {
  if (!next) return prev;
  if (prev.endsWith(TRUNCATED_SUFFIX)) return prev;
  if (prev.length + next.length <= limit) return prev + next;
  // limit 比标记本身还短时无法同时满足"加标记"和"不超上限"，此时不加标记
  if (limit <= TRUNCATED_SUFFIX.length) return prev.slice(0, Math.max(0, limit));
  return prev.slice(0, limit - TRUNCATED_SUFFIX.length) + TRUNCATED_SUFFIX;
}
