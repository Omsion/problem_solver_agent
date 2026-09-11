import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { MarkdownRenderer } from "./MarkdownRenderer";

/**
 * MarkdownRenderer 公式渲染回归测试。
 *
 * 线上现象：答案正文里直接显示原始 `$\|\mathbf{w}\|_2$`，即行内公式没被 KaTeX 渲染。
 * 这里锁定：
 * - 行内 / 块级公式都必须渲染出 KaTeX 的 DOM（`.katex` / `.katex-display`），
 *   且屏幕上不再残留原始定界符文本；
 * - KaTeX 不得进入报错态（`.katex-error` 会把原始公式打回屏幕，是同一现象的成因之一）；
 * - `inline` 模式去掉 `<p>` 包装（列表项、徽标里的短公式依赖它避免块级布局），默认模式仍保留。
 */
describe("MarkdownRenderer", () => {
  it("行内公式渲染为 KaTeX，且不残留原始 $ 文本", () => {
    const content = "范数是 $\\|\\mathbf{w}\\|_2$ 与 $\\lambda$";
    const { container } = render(<MarkdownRenderer content={content} />);

    // 两段行内公式都应渲染出 .katex 元素
    expect(container.querySelectorAll(".katex").length).toBeGreaterThanOrEqual(2);
    // 未进入 KaTeX 报错态（报错时原始公式会被原样显示，正是用户看到的现象）
    expect(container.querySelector(".katex-error")).toBeNull();
    // 原始定界符文本必须消失：这是本次回归的核心断言
    expect(container.textContent).not.toContain("$\\|");
    expect(container.textContent).not.toContain("$");
    // 公式之外的中文照常显示
    expect(container.textContent).toContain("范数是");
    expect(container.textContent).toContain("与");
  });

  it("块级公式渲染为 katex-display", () => {
    // 注意：remark-math 只在 $$ 定界符独占一行时按块级公式处理，
    // 因此这里用带换行的写法拿到真正的 display math。
    const { container } = render(<MarkdownRenderer content={"$$\nE = mc^2\n$$"} />);

    expect(container.querySelectorAll(".katex-display").length).toBe(1);
    expect(container.querySelectorAll(".katex").length).toBeGreaterThanOrEqual(1);
    expect(container.querySelector(".katex-error")).toBeNull();
    expect(container.textContent).not.toContain("$$");
  });

  it("单行 $$…$$ 归一化为块级公式（居中显示）", () => {
    // remark-math 原本把单行 "$$E = mc^2$$" 当成行内公式，居中与行距都不对；
    // normalizeMathDelimiters 会给它补上换行，因此这里必须是 katex-display。
    const { container } = render(<MarkdownRenderer content={"$$E = mc^2$$"} />);

    expect(container.querySelectorAll(".katex-display").length).toBe(1);
    expect(container.querySelectorAll(".katex").length).toBeGreaterThanOrEqual(1);
    expect(container.querySelector(".katex-error")).toBeNull();
    expect(container.textContent).not.toContain("$$");
  });

  it("行内 $…$ 不会被误升级为块级公式", () => {
    const { container } = render(<MarkdownRenderer content={"当 $x > 0$ 时成立"} />);

    expect(container.querySelectorAll(".katex-display").length).toBe(0);
    expect(container.querySelectorAll(".katex").length).toBeGreaterThanOrEqual(1);
    expect(container.textContent).toContain("当");
    expect(container.textContent).toContain("时成立");
  });

  it("inline 模式不渲染 <p> 包装，默认模式保留 <p>", () => {
    const inlineView = render(<MarkdownRenderer content="普通文本" inline />);
    expect(inlineView.container.querySelector("p")).toBeNull();
    expect(inlineView.container.textContent).toContain("普通文本");

    const blockView = render(<MarkdownRenderer content="普通文本" />);
    expect(blockView.container.querySelector("p")).not.toBeNull();
    expect(blockView.container.textContent).toContain("普通文本");
  });

  it("inline 模式下公式依然渲染（列表项/徽标场景）", () => {
    const { container } = render(<MarkdownRenderer content={"核对结论：$\\lambda > 0$"} inline />);

    expect(container.querySelector("p")).toBeNull();
    expect(container.querySelectorAll(".katex").length).toBeGreaterThanOrEqual(1);
    expect(container.textContent).not.toContain("$");
  });

  it("没有公式的纯文本按原文渲染", () => {
    const { container } = render(<MarkdownRenderer content="这是一段没有公式的普通文本" />);

    expect(container.textContent).toContain("这是一段没有公式的普通文本");
    expect(container.querySelector(".katex")).toBeNull();
    expect(container.querySelector(".katex-display")).toBeNull();
    expect(container.querySelector(".katex-error")).toBeNull();
  });
});
