import { lazy, Suspense } from "react";
import type { StageTimings } from "../../types";

/**
 * 重 Markdown 渲染器的懒加载边界。
 *
 * 首屏 JS 体积的大头是 react-markdown + remark/rehype + KaTeX（约 500 KB）。
 * 这些只在"真的要看解答"时才需要，所以放进动态分包；在解答出现之前，
 * 首屏只加载轻量外壳。
 */
const LazyMarkdownRenderer = lazy(() =>
  import("./MarkdownRenderer").then((m) => ({ default: m.MarkdownRenderer })),
);

const LazyReadingMode = lazy(() =>
  import("./ReadingMode").then((m) => ({ default: m.ReadingMode })),
);

const LazyTimingBreakdown = lazy(() =>
  import("./TimingBreakdown").then((m) => ({ default: m.TimingBreakdown })),
);

const Skeleton = ({ lines = 4 }: { lines?: number }) => (
  <div className="space-y-2 py-2" aria-busy="true" aria-label="正在渲染解答">
    {Array.from({ length: lines }).map((_, i) => (
      <div
        key={i}
        className="h-4 bg-gray-100 rounded animate-pulse"
        style={{ width: `${100 - (i % 3) * 12}%` }}
      />
    ))}
  </div>
);

export const LazyAnswer = ({ content }: { content: string }) => (
  <Suspense fallback={<Skeleton lines={6} />}>
    <LazyMarkdownRenderer content={content} />
  </Suspense>
);

/**
 * 小型 Markdown 渲染（答案卡、核对结论）。
 *
 * 渲染器是重包，所以先渲染纯文本、分片到位后再升级成富文本：首帧立刻可读，
 * 公式/表格随后补齐，不会出现"卡片先空白再闪一下"。
 */
export const LazyMarkdown = ({
  content,
  className = "",
  inline = false,
}: {
  content: string;
  className?: string;
  inline?: boolean;
}) => (
  <Suspense
    fallback={
      inline ? (
        <span className={className}>{content}</span>
      ) : (
        <p className={`whitespace-pre-wrap break-words ${className}`}>{content}</p>
      )
    }
  >
    <LazyMarkdownRenderer content={content} className={className} inline={inline} />
  </Suspense>
);

export const LazyReader = ({ content, onClose }: { content: string; onClose: () => void }) => (
  <Suspense fallback={null}>
    <LazyReadingMode content={content} onClose={onClose} />
  </Suspense>
);

export const LazyTimings = ({ timings }: { timings: StageTimings | null | undefined }) => (
  <Suspense fallback={null}>
    <LazyTimingBreakdown timings={timings} />
  </Suspense>
);
