import { beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OutputPanel } from "./OutputPanel";
import { ConfirmProvider } from "../ui/confirm";
import { emptyProgress, useTaskStore } from "../../stores/useTaskStore";
import type { ProgressState } from "../../types";

/**
 * 解答面板的排版与答案卡回归测试。
 *
 * 对应两个线上问题：
 * 1. 运行中"0 字符"和"取消任务"被挤在正文区的一行里，既难看又和"已用时"分家；
 *    现在状态（字符数 · 已用时）与操作（取消任务）都在「解答」那一行。
 * 2. 任务详情返回的是整个解答文件，前端自己在原文上"猜"答案时，卡片里出现了
 *    YAML frontmatter 与题面。
 */
const setProgress = (taskId: string, patch: Partial<ProgressState>) => {
  useTaskStore.setState({ progress: { [taskId]: { ...emptyProgress(), ...patch } } });
};

/** OutputPanel 用到确认弹窗（取消任务），必须包在 provider 里 */
const renderPanel = (taskId: string) =>
  render(
    <ConfirmProvider>
      <OutputPanel taskId={taskId} />
    </ConfirmProvider>,
  );

const FULL_FILE = [
  "---",
  "problem_type: ACM",
  "solver: deepseek (deepseek-flash)",
  "images:",
  "  - a.jpg",
  "---",
  "",
  "# 题目文本",
  "",
  "题面内容不应该出现在答案卡里",
  "",
  "---",
  "",
  "# 解答",
  "",
  "**核心任务**：实现梯度下降。",
].join("\n");

describe("OutputPanel 排版", () => {
  beforeEach(() => {
    useTaskStore.setState({ progress: {}, streams: {}, activeTaskId: null });
  });

  it("运行中：字符数、已用时与取消任务都在「解答」同一行", () => {
    setProgress("t1", {
      phase: "solving",
      message: "正在解题…",
      answer: "abc",
      startedAt: Date.now() - 65_000,
    });

    renderPanel("t1");

    const answerTab = screen.getByRole("button", { name: "解答" });
    const toolbar = answerTab.parentElement as HTMLElement;

    expect(toolbar).toContainElement(screen.getByText("3 字符"));
    expect(toolbar).toContainElement(screen.getByText(/已用时 1m5s/));
    expect(toolbar).toContainElement(screen.getByRole("button", { name: "取消任务" }));
  });

  it("运行中不再单独占一行显示「0 字符」", () => {
    setProgress("t1", { phase: "solving", message: "正在解题…", startedAt: Date.now() });

    renderPanel("t1");

    // 字符数为 0 时依然和计时在同一行，而不是另起一行
    const answerTab = screen.getByRole("button", { name: "解答" });
    expect(answerTab.parentElement).toContainElement(screen.getByText("0 字符"));
  });

  it("完成后展示后端抽取的答案卡，正文里的元信息不进卡片", () => {
    setProgress("t2", {
      phase: "done",
      answer: FULL_FILE,
      answerCard: { text: "选 C。购买概率 0.8123。", extracted: true, truncated: false },
    });

    renderPanel("t2");

    expect(screen.getByText("最终答案")).toBeInTheDocument();
    expect(screen.getByText("选 C。购买概率 0.8123。")).toBeInTheDocument();
    expect(screen.queryByText(/problem_type/)).not.toBeInTheDocument();
    expect(screen.queryByText(/题面内容不应该出现在答案卡里/)).not.toBeInTheDocument();
    // 抽取成功时不该再提示"自动提取"
    expect(screen.queryByText("自动提取")).not.toBeInTheDocument();
  });

  it("没有后端答案卡时，前端回退也不会把前言当答案", () => {
    setProgress("t3", { phase: "done", answer: FULL_FILE });

    renderPanel("t3");

    expect(screen.getByText(/核心任务/)).toBeInTheDocument();
    expect(screen.queryByText(/problem_type/)).not.toBeInTheDocument();
    expect(screen.queryByText(/题面内容不应该出现在答案卡里/)).not.toBeInTheDocument();
  });

  it("完整解答里不展示 YAML 元信息，正文照常保留", async () => {
    const user = userEvent.setup();
    setProgress("t4", {
      phase: "done",
      answer: FULL_FILE,
      answerCard: { text: "选 C。", extracted: true, truncated: false },
    });

    renderPanel("t4");
    await user.click(screen.getByRole("button", { name: /完整解答/ }));

    expect(await screen.findByText(/核心任务/)).toBeInTheDocument();
    expect(screen.queryByText(/problem_type/)).not.toBeInTheDocument();
  });

  it("后端卡片为空白时用解答正文兜底，绝不显示一张空卡片", () => {
    setProgress("t5", {
      phase: "done",
      answer: FULL_FILE,
      answerCard: { text: "   ", extracted: false, truncated: false },
    });

    renderPanel("t5");

    // 卡片有内容（前端从正文兜底抽出），而不是空白把完整解答挡住
    expect(screen.getByText(/核心任务/)).toBeInTheDocument();
  });

  it("完全抽不出内容时不渲染卡片，完整解答保持可见", async () => {
    setProgress("t6", {
      phase: "done",
      answer: "---\nproblem_type: ACM\nsolver: deepseek (deepseek-flash)\n---\n\n# 解答\n",
      answerCard: { text: "", extracted: false, truncated: false },
    });

    renderPanel("t6");

    expect(screen.queryByText("最终答案")).not.toBeInTheDocument();
    expect(await screen.findByText("完整解答")).toBeInTheDocument();
  });
});
