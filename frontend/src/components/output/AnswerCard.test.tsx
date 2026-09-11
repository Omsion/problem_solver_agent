import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AnswerCard } from "./AnswerCard";
import type { AnswerCard as AnswerCardData, VerificationResult } from "../../types";

const card = (overrides: Partial<AnswerCardData> = {}): AnswerCardData => ({
  text: "程序存储器与数据存储器",
  extracted: true,
  ...overrides,
});

const verifyResult = (overrides: Partial<VerificationResult> = {}): VerificationResult => ({
  verdict: "agree",
  issues: [],
  corrections: "",
  model: "GLM-4.6V",
  ...overrides,
});

const noop = () => {};

const renderCard = (props: Partial<Parameters<typeof AnswerCard>[0]> = {}) =>
  render(
    <AnswerCard
      card={card()}
      verification={null}
      verifying={false}
      onVerify={noop}
      onShowFull={noop}
      canVerify
      {...props}
    />,
  );

describe("AnswerCard", () => {
  it("展示最终答案文本", () => {
    renderCard({ card: card({ text: "选 B" }) });
    expect(screen.getByText("选 B")).toBeInTheDocument();
    expect(screen.getByText("最终答案")).toBeInTheDocument();
  });

  it("未命中「最终答案」小节时标注自动提取", () => {
    renderCard({ card: card({ extracted: false }) });
    expect(screen.getByText("自动提取")).toBeInTheDocument();
  });

  it("截断时给出提示", () => {
    renderCard({ card: card({ truncated: true }) });
    expect(screen.getByText("已截断")).toBeInTheDocument();
  });

  it("复制按钮写入剪贴板并给出反馈", async () => {
    const user = userEvent.setup();
    renderCard({ card: card({ text: "答案内容" }) });

    await user.click(screen.getByRole("button", { name: /复制答案/ }));

    // user-event 提供了剪贴板替身，直接断言其内容
    expect(await navigator.clipboard.readText()).toBe("答案内容");
    expect(await screen.findByText("已复制")).toBeInTheDocument();
  });

  it("点击完整解答触发回调", async () => {
    const user = userEvent.setup();
    const onShowFull = vi.fn();
    renderCard({ onShowFull });

    await user.click(screen.getByRole("button", { name: /完整解答/ }));
    expect(onShowFull).toHaveBeenCalledTimes(1);
  });

  it("核对中禁用按钮并显示进度文案", () => {
    renderCard({ verifying: true });
    const button = screen.getByRole("button", { name: /核对中/ });
    expect(button).toBeDisabled();
  });

  it("canVerify 为 false 时不展示核对入口", () => {
    renderCard({ canVerify: false });
    expect(screen.queryByRole("button", { name: /核对答案/ })).not.toBeInTheDocument();
  });

  it("核对通过时显示绿色结论", () => {
    renderCard({ verification: verifyResult({ verdict: "agree" }) });
    expect(screen.getByText("核对通过")).toBeInTheDocument();
    expect(screen.getByText("GLM-4.6V")).toBeInTheDocument();
  });

  it("核对发现疑点时列出问题与修正建议（考试场景最关键的信息）", () => {
    renderCard({
      verification: verifyResult({
        verdict: "disagree",
        issues: ["第 2 小问漏答", "选项 B 与题干要求矛盾"],
        corrections: "应选 A，并补上第二问的推导",
      }),
    });

    expect(screen.getByText("核对发现疑点")).toBeInTheDocument();
    expect(screen.getByText("第 2 小问漏答")).toBeInTheDocument();
    expect(screen.getByText("选项 B 与题干要求矛盾")).toBeInTheDocument();
    expect(screen.getByText(/应选 A/)).toBeInTheDocument();
    // 结论文案应变为"核对后答案"
    expect(screen.getByText("核对后答案")).toBeInTheDocument();
  });

  it("无法判定时展示原因而不显示修正", () => {
    renderCard({
      verification: verifyResult({ verdict: "unclear", reason: "题目图片信息不足" }),
    });
    expect(screen.getByText("无法判定")).toBeInTheDocument();
    expect(screen.getByText("题目图片信息不足")).toBeInTheDocument();
  });

  it("已有核对结果时可再次核对", () => {
    renderCard({ verification: verifyResult() });
    expect(screen.getByRole("button", { name: /重新核对/ })).toBeInTheDocument();
  });

  it("核对结论与建议修正里的公式会被渲染，而不是显示原始定界符", async () => {
    // 线上现象：核对结果里直接显示 $\|\mathbf{w}\|_2$，公式没有被 KaTeX 渲染
    const { container } = renderCard({
      verification: verifyResult({
        verdict: "disagree",
        issues: ["题目要求 $\\|\\mathbf{w}\\|_2$，解答里用的是 $\\|\\mathbf{w}\\|_2^2$"],
        corrections: "修正正则项为 $\\lambda \\|\\mathbf{w}\\|_2$，梯度需处理 $\\mathbf{w}=0$",
      }),
    });

    // Markdown 渲染器是懒加载的，等它把 KaTeX 挂上来
    await waitFor(() => {
      expect(container.querySelectorAll(".katex").length).toBeGreaterThanOrEqual(1);
    });
    expect(container.querySelector(".katex-error")).toBeNull();
    expect(container.textContent).not.toContain("$\\|");
  });
});
