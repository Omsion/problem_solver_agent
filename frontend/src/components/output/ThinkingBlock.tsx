import { useState } from "react";

interface Props {
  content: string;
  /** 仍在流式输出：只渲染末尾片段，避免每个分片都重排几百 KB 的文本 */
  streaming?: boolean;
}

/** 流式输出期间最多渲染的字符数（结束后显示全文） */
const STREAMING_TAIL_CHARS = 6000;

/**
 * 思考过程折叠块。
 *
 * 两个关键取舍：
 * 1. **按纯文本渲染**（`<pre>`），不走 Markdown/KaTeX。思考过程动辄十几万字，
 *    而且经常出现写了一半的公式；把它塞进 remark/rehype 会在主线程上跑几秒到
 *    几十秒——线上"点开思考过程页面卡死"就是这么来的。
 * 2. **流式期间只渲染末尾一段**：每个分片都重排整段超长文本同样会卡。
 */
export const ThinkingBlock = ({ content, streaming = false }: Props) => {
  const [expanded, setExpanded] = useState(false);

  if (!content) return null;

  const clipped = streaming && content.length > STREAMING_TAIL_CHARS;
  const shown = clipped ? content.slice(-STREAMING_TAIL_CHARS) : content;

  return (
    <div className="border border-indigo-200 rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-4 py-2.5 bg-indigo-50 hover:bg-indigo-100 transition-colors text-left cursor-pointer"
      >
        <svg
          className={`w-4 h-4 text-indigo-500 transition-transform ${expanded ? "rotate-90" : ""}`}
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span className="text-sm font-medium text-indigo-700">思考过程</span>
        {streaming && <span className="text-xs text-indigo-400">生成中…</span>}
        <span className="text-xs text-indigo-400 ml-auto tabular-nums">{content.length} 字符</span>
      </button>
      {expanded && (
        <div className="p-4 bg-gray-50 border-t border-indigo-100 max-h-96 overflow-y-auto">
          {clipped && (
            <p className="mb-2 text-xs text-gray-400">
              思考仍在进行，这里只显示最新 {STREAMING_TAIL_CHARS} 字符；生成结束后可查看全部。
            </p>
          )}
          <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-gray-700">
            {shown}
          </pre>
        </div>
      )}
    </div>
  );
};
