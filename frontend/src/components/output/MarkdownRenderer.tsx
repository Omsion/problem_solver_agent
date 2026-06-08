import { useMemo, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

interface Props {
  content: string;
  className?: string;
}

/**
 * 预处理 markdown 内容，将 LaTeX 风格分隔符标准化为 remark-math 可识别的格式：
 *   \( ... \) → $ ... $  (行内公式)
 *   \[ ... \] → $$ ... $$ (块级公式)
 */
function normalizeMathDelimiters(text: string): string {
  return text
    .replace(/\\\[/g, "$$$")
    .replace(/\\\]/g, "$$$")
    .replace(/\\\(/g, "$")
    .replace(/\\\)/g, "$");
}

export const MarkdownRenderer = ({ content, className = "" }: Props) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const normalized = useMemo(() => normalizeMathDelimiters(content), [content]);

  return (
    <div ref={containerRef} className={`markdown-body overflow-auto ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
      >
        {normalized}
      </ReactMarkdown>
    </div>
  );
};
