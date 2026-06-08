import { useEffect, useMemo, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

interface Props {
  content: string;
  className?: string;
}

function normalizeMathDelimiters(text: string): string {
  return text
    // 标准 LaTeX 分隔符
    .replace(/\\\[/g, "$$$")
    .replace(/\\\]/g, "$$$")
    .replace(/\\\(/g, "$")
    .replace(/\\\)/g, "$")
    // 自动包裹裸露的 \begin{...}...\end{...} 块为块级公式
    .replace(/(\\begin\{[^}]+\}[\s\S]*?\\end\{[^}]+\})/g, "\n$$\n$1\n$$\n");
}

const SCROLL_THRESHOLD = 80;

export const MarkdownRenderer = ({ content, className = "" }: Props) => {
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
