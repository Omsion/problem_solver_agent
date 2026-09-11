import { describe, expect, it } from "vitest";
import { appendBounded, MAX_BUFFER_CHARS, TRUNCATED_SUFFIX } from "./streamBuffer";

describe("appendBounded", () => {
  it("未超上限时正常拼接", () => {
    expect(appendBounded("你好", "世界")).toBe("你好世界");
  });

  it("空分片不改变内容", () => {
    expect(appendBounded("abc", "")).toBe("abc");
  });

  it("超过上限后截断并追加标记", () => {
    const limit = TRUNCATED_SUFFIX.length + 10;
    const result = appendBounded("a".repeat(limit), "b".repeat(10), limit);
    expect(result.length).toBeLessThanOrEqual(limit);
    expect(result.endsWith(TRUNCATED_SUFFIX)).toBe(true);
  });

  it("截断结果始终不超过上限（含标记长度）", () => {
    const limits = [TRUNCATED_SUFFIX.length + 5, TRUNCATED_SUFFIX.length + 50, 200];
    for (const limit of limits) {
      const result = appendBounded("x".repeat(limit * 2), "y", limit);
      expect(result.length).toBeLessThanOrEqual(limit);
      expect(result.endsWith(TRUNCATED_SUFFIX)).toBe(true);
    }
  });

  it("上限小于标记长度时不加标记，只做硬截断", () => {
    const tiny = 5;
    const result = appendBounded("x".repeat(100), "y", tiny);
    expect(result.length).toBeLessThanOrEqual(tiny);
    expect(result.endsWith(TRUNCATED_SUFFIX)).toBe(false);
  });

  it("已截断后不再继续累积（避免长任务 CPU 浪费）", () => {
    const truncated = appendBounded("a".repeat(10), "b".repeat(10), 12);
    const again = appendBounded(truncated, "更多内容", 12);
    expect(again).toBe(truncated);
  });

  it("边界值：正好等于上限时不截断", () => {
    const result = appendBounded("a".repeat(5), "b".repeat(5), 10);
    expect(result).toBe("a".repeat(5) + "b".repeat(5));
    expect(result.endsWith(TRUNCATED_SUFFIX)).toBe(false);
  });

  it("默认上限是一个合理的大值（约 200KB）", () => {
    expect(MAX_BUFFER_CHARS).toBe(200_000);
  });
});
