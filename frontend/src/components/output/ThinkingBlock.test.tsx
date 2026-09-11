import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThinkingBlock } from "./ThinkingBlock";

/**
 * 思考过程折叠块回归测试。
 *
 * 线上现象：点开思考过程页面卡死——思考内容动辄十几万字，还被塞进
 * remark/rehype + KaTeX 渲染，主线程直接卡住。这里锁定两条修复：
 * 按纯文本渲染、流式期间只渲染末尾片段。
 */
describe("ThinkingBlock", () => {
  it("默认折叠，显示字符数", () => {
    const { container } = render(<ThinkingBlock content="一二三" />);
    expect(screen.getByText("3 字符")).toBeInTheDocument();
    expect(container.querySelector("pre")).toBeNull();
  });

  it("展开后按纯文本渲染，不使用 Markdown 渲染器", async () => {
    const user = userEvent.setup();
    const { container } = render(<ThinkingBlock content={"# 标题\n$\\|w\\|_2$"} />);

    await user.click(screen.getByRole("button"));

    const pre = container.querySelector("pre");
    expect(pre?.textContent).toBe("# 标题\n$\\|w\\|_2$");
    // 没有走 Markdown 渲染器（那是最主要的卡顿来源）
    expect(container.querySelector(".markdown-body")).toBeNull();
  });

  it("流式期间只渲染末尾片段并给出说明", () => {
    const { container } = render(<ThinkingBlock content={"A".repeat(9000) + "尾巴"} streaming />);
    fireEvent.click(screen.getByRole("button"));

    const pre = container.querySelector("pre");
    expect(pre?.textContent?.length).toBe(6000);
    expect(pre?.textContent?.endsWith("尾巴")).toBe(true);
    expect(screen.getByText(/只显示最新/)).toBeInTheDocument();
    expect(screen.getByText("生成中…")).toBeInTheDocument();
  });

  it("生成结束后渲染全文且不再提示片段", () => {
    const { container } = render(<ThinkingBlock content={"A".repeat(9000)} streaming={false} />);
    fireEvent.click(screen.getByRole("button"));

    expect(container.querySelector("pre")?.textContent?.length).toBe(9000);
    expect(screen.queryByText(/只显示最新/)).not.toBeInTheDocument();
  });

  it("内容为空时不渲染", () => {
    const { container } = render(<ThinkingBlock content="" />);
    expect(container.firstChild).toBeNull();
  });
});
