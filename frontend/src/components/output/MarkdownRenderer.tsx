import { useEffect, useMemo, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { CodeBlock } from "./CodeBlock";

interface Props {
  content: string;
  className?: string;
  /**
   * 行内渲染：不包 `<p>`，用于列表项、徽标里的短文本（例如核对结论里的公式）。
   * 默认 false = 标准块级 Markdown。
   */
  inline?: boolean;
}

export function normalizeMathDelimiters(text: string): string {
  // 注意：替换串里的 `$$` 会被 String.replace 解释成"一个字面 $"，所以块级公式
  // 必须用函数式替换，否则 "$$\n...\n$$" 会被写成 "$...$"（行内公式），
  // 公式既不居中、行距也不对。
  const asDisplayMath = (_match: string, body: string) => `\n$$\n${body}\n$$\n`;

  return text
    // 标准 LaTeX 分隔符
    .replace(/\\\[/g, "$$$")
    .replace(/\\\]/g, "$$$")
    .replace(/\\\(/g, "$")
    .replace(/\\\)/g, "$")
    // 自动包裹裸露的 \begin{...}...\end{...} 块为块级公式
    .replace(/(\\begin\{[^}]+\}[\s\S]*?\\end\{[^}]+\})/g, asDisplayMath)
    // 单行 $$...$$ 补上换行：remark-math 只在定界符独占一行时按块级（display）公式处理，
    // 否则会当成行内公式，居中排版与行间间距都不对。模型经常写成单行。
    .replace(/\$\$[ \t]*([^\n$]+?)[ \t]*\$\$/g, asDisplayMath);
}

const SCROLL_THRESHOLD = 80;

/** 行内模式去掉 react-markdown 给段落加的 `<p>` 包装 */
const InlineParagraph = ({ children }: { children?: React.ReactNode }) => <>{children}</>;

export const MarkdownRenderer = ({ content, className = "", inline = false }: Props) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const normalized = useMemo(() => normalizeMathDelimiters(content), [content]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const { scrollTop, scrollHeight, clientHeight } = el;
    const isNearBottom = scrollHeight - scrollTop - clientHeight < SCROLL_THRESHOLD;
    if (isNearBottom) {
      el.scrollTop = scrollHeight;
    }
  }, [content]);

  return (
    <div ref={containerRef} className={`markdown-body ${inline ? "" : "overflow-auto"} ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={inline ? { code: CodeBlock, p: InlineParagraph } : { code: CodeBlock }}
      >
        {normalized}
      </ReactMarkdown>
    </div>
  );
};
