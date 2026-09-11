import type { AnswerCard } from "../types";

/** 兜底答案卡的长度上限：避免把整篇解答塞进卡片 */
export const FALLBACK_CARD_CHARS = 400;

/**
 * 解答文件开头的 YAML frontmatter（内部记账信息：题型/模型/时间/图片名）。
 *
 * 只在这种"键: 值"块上生效，避免把正文里恰好成对出现的 `---`（Markdown 分隔线）
 * 当成元信息删掉。
 */
const FRONTMATTER_KEYS = /^(?:problem_type|solver|aux_model|status|created|images):/m;
/** 元信息块的结束行：独占一行的 `---` */
const FRONTMATTER_END = /\r?\n---[ \t]*(?:\r?\n|$)/;
/** 小节标题行（Markdown 标题、粗体小节名、分隔线）：答案卡到这里为止 */
const SECTION_END = /^[ \t]*(?:#{1,6}[ \t]+\S|\*\*\S|[-*_]{3,})[^\n]*$/m;
/** 包住整篇解答的「# 解答」标题 */
const ANSWER_HEADING = /^[ \t]*#{1,6}[ \t]*解答[ \t]*$/m;

/**
 * 去掉解答文件开头的 YAML 元信息，保留「题目文本」与「解答」正文。
 *
 * 阅读模式和"完整解答"展示的是整个解答文件，元信息对读者是纯噪音。
 */
export function stripFrontmatter(text: string): string {
  const body = (text ?? "").replace(/^\uFEFF/, "").replace(/^\s+/, "");
  if (!body.startsWith("---")) return text ?? "";

  const end = FRONTMATTER_END.exec(body);
  if (!end) return text ?? "";
  // 内容不像元信息（例如正文里的第一条分隔线）就原样返回
  if (!FRONTMATTER_KEYS.test(body.slice(0, end.index))) return text ?? "";

  return body.slice(end.index + end[0].length).replace(/^\s+/, "");
}

/**
 * 去掉解答文件的前言：YAML frontmatter、「题目文本」小节、「# 解答」标题。
 *
 * 后端 REST 返回的是**整个解答文件**，若直接在原文里找「最终答案」，卡片里就会
 * 出现 problem_type / solver / 题面这些与答案无关的内容（线上就是这么翻车的）。
 * 逻辑与 `problem_solver_agent/answer_card.py` 的 `_strip_preamble` 保持一致：
 * 优先按 `# 解答` 标题切，因为题面里可能出现独立的 `---` 行（"输入输出格式"一节）。
 */
export function stripPreamble(text: string): string {
  const normalized = (text ?? "")
    .replace(/\r\n?/g, "\n")
    .replace(/^\uFEFF/, "")
    .replace(/^\n+/, "");

  const heading = ANSWER_HEADING.exec(normalized);
  if (heading) {
    return normalized.slice(heading.index + heading[0].length).replace(/^\s+/, "").trim();
  }

  // 旧文件（没有「# 解答」标题）：按 frontmatter + 题目文本小节 + 分隔线剥离。
  // 只有真的剥掉了前言，才继续去掉那条分隔线——否则会把正文里成对的 `---`
  // 当成元信息删掉。
  let cleaned = normalized;
  let strippedPreamble = false;

  const end = FRONTMATTER_END.exec(cleaned);
  if (cleaned.startsWith("---") && end && FRONTMATTER_KEYS.test(cleaned.slice(0, end.index))) {
    cleaned = cleaned.slice(end.index + end[0].length);
    strippedPreamble = true;
  }

  const withoutProblemText = cleaned.replace(
    /^[ \t]*#{1,6}[ \t]*题目文本[^\n]*\n[\s\S]*?(?=^[ \t]*-{3,}[ \t]*$)/m,
    "",
  );
  if (withoutProblemText !== cleaned) {
    cleaned = withoutProblemText;
    strippedPreamble = true;
  }

  if (strippedPreamble) {
    cleaned = cleaned.replace(/^\s*(?:[ \t]*-{3,}[ \t]*\n+)+/, "");
  }
  return cleaned.trim();
}

/**
 * 从整篇解答里"猜"一张答案卡：优先「最终答案」小节，否则取第一段正文。
 *
 * 只在前端拿不到后端抽取结果时使用（旧任务、流式中途刷新）。正常情况下
 * `answer_card` 由后端给出（SSE `done` 事件或任务详情接口），两边同源。
 */
export function fallbackAnswerCard(answer: string, maxChars = FALLBACK_CARD_CHARS): AnswerCard {
  const text = stripPreamble(answer ?? "");
  if (!text) return { text: "", extracted: false, truncated: false };

  const heading =
    /^[ \t]*(?:#{1,6}[ \t]*)?\*{0,2}[ \t]*(?:最终答案|参考答案|结论)[ \t]*\*{0,2}[ \t]*[:：]?[ \t]*$/m.exec(
      text,
    );
  if (heading) {
    const rest = text.slice(heading.index + heading[0].length).replace(/^\s+/, "");
    // 到下一个小节标志为止，别把整篇解答都塞进卡片
    const next = SECTION_END.exec(rest);
    const body = (next ? rest.slice(0, next.index) : rest).trim();
    if (body) {
      return {
        text: body.slice(0, maxChars),
        extracted: true,
        truncated: body.length > maxChars,
      };
    }
  }

  const paragraph =
    text
      .split(/\n\s*\n/)
      .map((block) => block.trim())
      .find((block) => block && !block.startsWith("#")) ?? text;

  return {
    text: paragraph.slice(0, maxChars),
    extracted: false,
    truncated: paragraph.length > maxChars,
  };
}
