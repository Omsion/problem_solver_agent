import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createTaskStream, type StreamStatus } from "./taskStream";
import { FakeEventSource } from "../../test/fakeEventSource";

type Recorded = { status: StreamStatus; attempt?: number; reason?: string };

/**
 * SSE 连接层测试。
 *
 * 覆盖缺陷 C 的核心：断线必须自动重连，且要暴露状态给界面，
 * 不能再出现"一次失败就永久停在等待任务开始"。
 */
describe("createTaskStream", () => {
  const url = (id: string) => `/api/tasks/${id}/stream`;
  let statuses: Recorded[];
  let events: Array<{ type: string; [key: string]: unknown }>;

  beforeEach(() => {
    vi.useFakeTimers();
    FakeEventSource.reset();
    statuses = [];
    events = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    vi.useRealTimers();
    FakeEventSource.reset();
  });

  const make = (taskId = "t1") =>
    createTaskStream(taskId, url, {
      onEvent: (event) => events.push(event as never),
      onStatus: (status, info) => statuses.push({ status, ...info }),
    });

  it("建立连接后报告 open 状态", () => {
    const stream = make();
    FakeEventSource.last.open();
    expect(statuses.at(-1)?.status).toBe("open");
    stream.close();
  });

  it("收到业务事件时回调，并解析 JSON", () => {
    const stream = make();
    FakeEventSource.last.emit("chunk", { type: "chunk", content: "你好" });
    expect(events).toHaveLength(1);
    expect(events[0].content).toBe("你好");
    stream.close();
  });

  it("忽略无法解析的负载（例如心跳）", () => {
    const stream = make();
    FakeEventSource.last.emit("chunk", "这不是 JSON");
    expect(events).toHaveLength(0);
    stream.close();
  });

  it("断线后自动重连，且重连次数递增", () => {
    const stream = make();
    FakeEventSource.last.open();
    const first = FakeEventSource.last;

    first.fail();
    // 进入重连等待
    const reconnecting = statuses.filter((s) => s.status === "reconnecting");
    expect(reconnecting.length).toBeGreaterThan(0);
    expect(reconnecting.at(-1)?.attempt).toBe(1);

    vi.advanceTimersByTime(1000);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.last).not.toBe(first);

    // 再断一次，退避时间翻倍（第二次尝试）
    FakeEventSource.last.fail();
    expect(statuses.at(-1)?.attempt).toBe(2);
    stream.close();
  });

  it("收到终态事件后不再重连", () => {
    make();
    FakeEventSource.last.open();
    FakeEventSource.last.emit("done", { type: "done", filename: "a.md" });

    expect(statuses.at(-1)?.status).toBe("closed");
    const instancesBefore = FakeEventSource.instances.length;

    // 即便随后触发 error，也不应再建新连接
    FakeEventSource.last.fail();
    vi.advanceTimersByTime(5000);
    expect(FakeEventSource.instances).toHaveLength(instancesBefore);
  });

  it("error 事件同样视为终态", () => {
    make();
    FakeEventSource.last.emit("error", { type: "error", message: "失败" });
    expect(events.at(-1)?.type).toBe("error");
    expect(statuses.at(-1)?.status).toBe("closed");
  });

  it("cancelled 事件视为终态", () => {
    make();
    FakeEventSource.last.emit("cancelled", { type: "cancelled", message: "已取消" });
    expect(events.at(-1)?.type).toBe("cancelled");
    expect(statuses.at(-1)?.status).toBe("closed");
  });

  it("retry() 立即重建连接（用户点重试）", () => {
    const stream = make();
    FakeEventSource.last.open();
    FakeEventSource.last.fail();
    vi.advanceTimersByTime(500); // 仍在退避等待中

    const before = FakeEventSource.instances.length;
    stream.retry();
    expect(FakeEventSource.instances.length).toBe(before + 1);
    expect(FakeEventSource.last.closed).toBe(false);
    stream.close();
  });

  it("close() 后不再重连", () => {
    const stream = make();
    FakeEventSource.last.open();
    stream.close();
    expect(FakeEventSource.last.closed).toBe(true);
    expect(stream.status()).toBe("closed");

    const before = FakeEventSource.instances.length;
    vi.advanceTimersByTime(30000);
    expect(FakeEventSource.instances).toHaveLength(before);
  });

  it("多次失败后标记为 failed，但仍保持自动重连", () => {
    const stream = make();
    for (let attempt = 0; attempt < 5; attempt += 1) {
      FakeEventSource.last.fail();
      vi.advanceTimersByTime(20000);
    }
    expect(statuses.some((s) => s.status === "failed")).toBe(true);
    stream.close();
  });

  it("页面重新可见时立即重连（不再等退避）", () => {
    const stream = make();
    FakeEventSource.last.open();
    FakeEventSource.last.fail();

    // 模拟连接已被彻底丢弃（onerror 后 teardown）
    const before = FakeEventSource.instances.length;
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    document.dispatchEvent(new Event("visibilitychange"));

    expect(FakeEventSource.instances.length).toBeGreaterThan(before);
    stream.close();
  });
});
