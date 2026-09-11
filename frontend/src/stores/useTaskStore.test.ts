import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useTaskStore } from "./useTaskStore";
import { FakeEventSource } from "../test/fakeEventSource";

/**
 * 任务 store 与流事件的打通测试。
 *
 * 这里验证的是"服务端事件 → 界面状态"的映射，包括答案卡与核对结果
 * 这些新增字段，以及取消/错误等终态。
 */
describe("useTaskStore 流式状态", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", FakeEventSource);
    FakeEventSource.reset();
    useTaskStore.setState({
      activeTaskId: null,
      progress: {},
      streams: {},
      globalConnection: null,
      seenAutoImportedTasks: new Set(),
      remoteConnected: false,
    });
  });

  afterEach(() => {
    FakeEventSource.reset();
    vi.unstubAllGlobals();
  });

  const progressOf = (taskId: string) => useTaskStore.getState().progress[taskId];

  it("init 事件把阶段推进到分类", () => {
    useTaskStore.getState().connectSSE("t1");
    FakeEventSource.last.emit("init", { type: "init", task_id: "t1", num_images: 3 });

    const progress = progressOf("t1");
    expect(progress.phase).toBe("classifying");
    expect(progress.message).toContain("3");
  });

  it("status 事件更新阶段与文案", () => {
    useTaskStore.getState().connectSSE("t1");
    FakeEventSource.last.emit("status", { type: "status", phase: "ocr", message: "正在识别第 2/5 页" });

    expect(progressOf("t1").phase).toBe("ocr");
    expect(progressOf("t1").message).toBe("正在识别第 2/5 页");
  });

  it("chunk 事件累积解答内容", () => {
    useTaskStore.getState().connectSSE("t1");
    FakeEventSource.last.emit("chunk", { type: "chunk", content: "第一段" });
    FakeEventSource.last.emit("chunk", { type: "chunk", content: "第二段" });

    expect(progressOf("t1").answer).toBe("第一段第二段");
  });

  it("reasoning 事件累积思考过程，与解答分开存放", () => {
    useTaskStore.getState().connectSSE("t1");
    FakeEventSource.last.emit("reasoning", { type: "reasoning", content: "思考中" });

    expect(progressOf("t1").thinking).toBe("思考中");
    expect(progressOf("t1").answer).toBe("");
  });

  it("done 事件写入答案卡与文件名", () => {
    useTaskStore.getState().connectSSE("t1");
    FakeEventSource.last.emit("done", {
      type: "done",
      task_id: "t1",
      filename: "21_设备故障预测.md",
      answer_card: { text: "选 B", extracted: true },
      timings: { solve: 1200, total: 2000 },
    });

    const progress = progressOf("t1");
    expect(progress.phase).toBe("done");
    expect(progress.filename).toBe("21_设备故障预测.md");
    expect(progress.answerCard?.text).toBe("选 B");
    expect(progress.timings?.total).toBe(2000);
    expect(progress.streamStatus).toBe("closed");
    // 终态后连接应从注册表移除，允许后续重试
    expect(useTaskStore.getState().streams.t1).toBeUndefined();
  });

  it("verified 事件写入核对结果", () => {
    useTaskStore.getState().connectSSE("t1");
    FakeEventSource.last.emit("verified", {
      type: "verified",
      task_id: "t1",
      verification: { verdict: "disagree", issues: ["漏答第二问"], corrections: "补上" },
    });

    const progress = progressOf("t1");
    expect(progress.verification?.verdict).toBe("disagree");
    expect(progress.verification?.issues).toEqual(["漏答第二问"]);
    expect(progress.verifying).toBe(false);
  });

  it("cancelled 事件进入独立状态而不是 error", () => {
    useTaskStore.getState().connectSSE("t1");
    FakeEventSource.last.emit("cancelled", { type: "cancelled", message: "任务已取消" });

    expect(progressOf("t1").phase).toBe("cancelled");
    expect(progressOf("t1").error).toBeNull();
  });

  it("error 事件带上错误信息", () => {
    useTaskStore.getState().connectSSE("t1");
    FakeEventSource.last.emit("error", { type: "error", task_id: "t1", message: "OCR 失败" });

    expect(progressOf("t1").phase).toBe("error");
    expect(progressOf("t1").error).toBe("OCR 失败");
  });

  it("同一任务重复 connectSSE 不会建立第二条连接（幂等）", () => {
    useTaskStore.getState().connectSSE("t1");
    const count = FakeEventSource.instances.length;
    useTaskStore.getState().connectSSE("t1");
    expect(FakeEventSource.instances.length).toBe(count);
  });

  it("断线时暴露重连状态供界面提示", () => {
    vi.useFakeTimers();
    try {
      useTaskStore.getState().connectSSE("t1");
      FakeEventSource.last.fail();

      const progress = progressOf("t1");
      expect(progress.streamStatus).toBe("reconnecting");
      expect(progress.reconnectAttempt).toBeGreaterThan(0);
      expect(progress.message).toContain("重连");
    } finally {
      vi.useRealTimers();
    }
  });

  it("resetProgress 清空该任务的所有状态", () => {
    useTaskStore.getState().connectSSE("t1");
    FakeEventSource.last.emit("chunk", { type: "chunk", content: "内容" });
    useTaskStore.getState().resetProgress("t1");

    const progress = progressOf("t1");
    expect(progress.answer).toBe("");
    expect(progress.answerCard).toBeNull();
    expect(progress.verification).toBeNull();
    expect(progress.phase).toBe("idle");
  });

  it("ensureProgress 为没有进度的任务建初始条目", () => {
    expect(useTaskStore.getState().ensureProgress("t9")).toBe(true);

    expect(progressOf("t9").phase).toBe("idle");
    expect(progressOf("t9").thinking).toBe("");
  });

  it("ensureProgress 不会清空已有进度（来回切换任务不丢思考过程与解答）", () => {
    useTaskStore.getState().connectSSE("t1");
    FakeEventSource.last.emit("reasoning", { type: "reasoning", content: "思考 A" });
    FakeEventSource.last.emit("chunk", { type: "chunk", content: "解答 A" });
    useTaskStore.getState().updateProgress("t1", { phase: "solving", startedAt: 1_700_000_000_000 });

    // 切到 t2 再切回 t1：TaskPage 会对 t1 调一次 ensureProgress
    useTaskStore.getState().connectSSE("t2");
    expect(useTaskStore.getState().ensureProgress("t1")).toBe(false);

    const progress = progressOf("t1");
    expect(progress.thinking).toBe("思考 A");
    expect(progress.answer).toBe("解答 A");
    expect(progress.phase).toBe("solving");
    // 计时起点同样保留，否则"已用时"会归零
    expect(progress.startedAt).toBe(1_700_000_000_000);
  });

  it("disconnectSSE 关闭连接并移除注册", () => {
    useTaskStore.getState().connectSSE("t1");
    const source = FakeEventSource.last;

    useTaskStore.getState().disconnectSSE("t1");
    expect(source.closed).toBe(true);
    expect(useTaskStore.getState().streams.t1).toBeUndefined();
  });
});
