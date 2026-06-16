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

export const LazyThinking = ({ content }: { content: string }) => (
  <Suspense fallback={<Skeleton />}>
    <LazyMarkdownRenderer content={content} className="text-sm" />
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
