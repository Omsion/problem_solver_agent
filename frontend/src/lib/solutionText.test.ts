import { describe, expect, it } from "vitest";
import { fallbackAnswerCard, stripFrontmatter, stripPreamble } from "./solutionText";

/**
 * 解答文本工具测试。
 *
 * 线上现象：任务详情接口返回的是**整个解答文件**，前端直接在原文上"猜"最终答案，
 * 于是答案卡里显示了 YAML frontmatter（problem_type / solver / images）和题面。
 */
const FULL_FILE = [
  "---",
  "problem_type: ACM",
  "solver: deepseek (deepseek-flash)",
  "created: 2026-09-12 03:50:58",
  "images:",
  "  - 22-1.jpg",
  "---",
  "",
  "# 题目文本",
  "",
  "# 22、商品购买预测",
  "",
  "## 题目描述",
  "实现一个二分类逻辑回归模型。",
  "",
  "---",
  "",
  "# 解答",
  "",
  "## 最终答案",
  "选 C。购买概率 0.8123。",
  "",
].join("\n");

describe("stripPreamble", () => {
  it("剥掉 frontmatter、「题目文本」小节与「# 解答」标题", () => {
    const cleaned = stripPreamble(FULL_FILE);
    expect(cleaned).not.toContain("problem_type");
    expect(cleaned).not.toContain("题目描述");
    expect(cleaned).toContain("## 最终答案");
  });

  it("CRLF 换行同样处理", () => {
    expect(stripPreamble(FULL_FILE.replace(/\n/g, "\r\n"))).toContain("最终答案");
  });

  it("纯模型输出保持不变", () => {
    expect(stripPreamble("选 B。")).toBe("选 B。");
  });
});

describe("stripFrontmatter", () => {
  it("只去掉元信息，保留题目文本与解答正文", () => {
    const cleaned = stripFrontmatter(FULL_FILE);
    expect(cleaned).not.toContain("problem_type");
    expect(cleaned).toContain("# 题目文本");
    expect(cleaned).toContain("## 最终答案");
  });

  it("不误伤正文里成对出现的分隔线", () => {
    const body = "# 解答\n\n上\n\n---\n\n下";
    expect(stripFrontmatter(body)).toBe(body);
  });

  it("去掉 BOM 后仍能识别元信息", () => {
    expect(stripFrontmatter("\uFEFF" + FULL_FILE)).not.toContain("problem_type");
  });
});

describe("stripPreamble 的边界", () => {
  it("BOM / 前导空行都不会让元信息漏进卡片", () => {
    for (const prefix of ["\uFEFF", "\n\n", "\uFEFF\n"]) {
      const cleaned = stripPreamble(prefix + FULL_FILE);
      expect(cleaned, prefix).not.toContain("problem_type");
      expect(cleaned, prefix).toContain("选 C");
    }
  });

  it("题面里有独立 --- 行时，仍然在「# 解答」处切干净", () => {
    const file = [
      "---",
      "problem_type: ACM",
      "---",
      "",
      "# 题目文本",
      "",
      "第一段题面",
      "---",
      "## 最终答案",
      "这是题面里对输出格式的说明",
      "---",
      "",
      "# 解答",
      "",
      "## 最终答案",
      "真正的答案：选 C。",
    ].join("\n");

    const card = fallbackAnswerCard(file);
    expect(card.text.startsWith("真正的答案")).toBe(true);
    expect(card.text).not.toContain("题面里对输出格式的说明");
  });

  it("不把正文里成对的分隔线当元信息删掉", () => {
    const body = "---\n\n我的推导\n\n---\n\n选 B。";
    expect(stripPreamble(body)).toBe(body);
  });

  it("答案卡不会吞掉整篇解答（到下一个小节为止）", () => {
    const file = FULL_FILE + "\n## 知识点解析\n补充说明。\n";
    const card = fallbackAnswerCard(file);
    expect(card.text).toContain("选 C");
    expect(card.text).not.toContain("补充说明");
  });
});

describe("fallbackAnswerCard", () => {
  it("优先取「最终答案」小节而不是文件元信息", () => {
    const card = fallbackAnswerCard(FULL_FILE);
    expect(card.extracted).toBe(true);
    expect(card.text.startsWith("选 C")).toBe(true);
    expect(card.text).not.toContain("problem_type");
  });

  it("没有「最终答案」小节时取解答的第一段正文", () => {
    const file = FULL_FILE.replace("## 最终答案\n选 C。购买概率 0.8123。", "**核心任务**：实现梯度下降。");
    const card = fallbackAnswerCard(file);
    expect(card.extracted).toBe(false);
    expect(card.text.startsWith("**核心任务**")).toBe(true);
    expect(card.text).not.toContain("题目描述");
  });

  it("超长内容标记截断", () => {
    const card = fallbackAnswerCard("## 最终答案\n" + "很长。".repeat(400), 50);
    expect(card.truncated).toBe(true);
    expect(card.text.length).toBe(50);
  });

  it("空文本返回空卡片", () => {
    expect(fallbackAnswerCard("").text).toBe("");
  });
});
