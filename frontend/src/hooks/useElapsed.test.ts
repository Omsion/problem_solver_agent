import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useElapsed } from "./useElapsed";

/**
 * 「已用时」计时回归测试。
 *
 * 线上现象：切换任务后"已用时"归零；回到仍在处理的任务时数字与真实耗时无关。
 * 根因是起点取了挂载时刻（`Date.now()`）而不是任务的真实开始时间。
 */
describe("useElapsed", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-12T03:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("以任务真实开始时间为起点，并随时间递增", () => {
    const { result } = renderHook(({ id, at }) => useElapsed(true, at, id), {
      initialProps: { id: "t1", at: Date.now() - 90_000 },
    });

    expect(result.current).toBe(90_000);
    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(result.current).toBe(91_000);
  });

  it("切到另一个任务时按新任务的起点计算，而不是从 0 重新开始", () => {
    const { result, rerender } = renderHook(({ id, at }) => useElapsed(true, at, id), {
      initialProps: { id: "t1", at: Date.now() - 90_000 },
    });
    expect(result.current).toBe(90_000);

    rerender({ id: "t2", at: Date.now() - 10_000 });

    expect(result.current).toBe(10_000);
  });

  it("切回同一个任务不会丢进度（起点不变）", () => {
    const at = Date.now() - 30_000;
    const { result, rerender } = renderHook(({ id, val }) => useElapsed(true, val, id), {
      initialProps: { id: "t1", val: at },
    });
    act(() => {
      vi.advanceTimersByTime(2_000);
    });
    expect(result.current).toBe(32_000);

    // 切走再切回来：startedAt 仍是任务真实开始时间
    rerender({ id: "t2", val: Date.now() - 5_000 });
    rerender({ id: "t1", val: at });

    expect(result.current).toBe(32_000);
  });

  it("缺少 startedAt 时从首次观察到运行的时刻开始计时", () => {
    const { result } = renderHook(() => useElapsed(true, null, "t1"));

    expect(result.current).toBe(0);
    act(() => {
      vi.advanceTimersByTime(2_500);
    });
    expect(result.current).toBe(2_500);
  });

  it("任务不再运行（或已完成）时归零", () => {
    const { result, rerender } = renderHook(
      ({ running }) => useElapsed(running, Date.now() - 5_000, "t1"),
      { initialProps: { running: true } },
    );
    expect(result.current).toBe(5_000);

    rerender({ running: false });

    expect(result.current).toBe(0);
  });
});
