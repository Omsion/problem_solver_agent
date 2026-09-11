import { useState, useEffect, useCallback, useRef } from "react";
import { MarkdownRenderer } from "./MarkdownRenderer";

interface Props {
  content: string;
  onClose: () => void;
}

const MIN_FONT_SIZE = 14;
const MAX_FONT_SIZE = 22;
const FONT_STEP = 2;
/** 顶部热区高度（px）：鼠标进入这个范围内才显示工具栏 */
const TOOLBAR_HOT_ZONE = 96;
/** 进入阅读模式时工具栏短暂亮相的时长（之后自动收起） */
const INITIAL_PEEK_MS = 2500;
/** 触屏点击后工具栏停留时长 */
const TOUCH_HOLD_MS = 3000;

export const ReadingMode = ({ content, onClose }: Props) => {
  const [fontSize, setFontSize] = useState(18);
  const [isDark, setIsDark] = useState(false);
  // 默认只短暂显示：阅读时正文区域必须干净，工具栏不能一直杵在那儿
  const [toolbarVisible, setToolbarVisible] = useState(true);
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Lock body scroll
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, []);

  // 进入时亮一下再收起，让用户知道这里有操作入口
  useEffect(() => {
    const timer = setTimeout(() => setToolbarVisible(false), INITIAL_PEEK_MS);
    return () => clearTimeout(timer);
  }, []);

  useEffect(
    () => () => {
      if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
    },
    [],
  );

  // 鼠标停在顶部热区才显示；移到正文区域立即收起
  const handleMouseMove = useCallback((event: React.MouseEvent<HTMLDivElement>) => {
    setToolbarVisible(event.clientY <= TOOLBAR_HOT_ZONE);
  }, []);

  // 触屏没有 hover：点顶部热区显示几秒后自动收回。
  // 注意只认热区内的触摸——否则每次滚动页面都会把工具栏"抖"出来。
  const handleTouchStart = useCallback((event: React.TouchEvent<HTMLDivElement>) => {
    const y = event.touches[0]?.clientY ?? Number.POSITIVE_INFINITY;
    if (y > TOOLBAR_HOT_ZONE) {
      setToolbarVisible(false);
      return;
    }
    setToolbarVisible(true);
    if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
    hideTimerRef.current = setTimeout(() => setToolbarVisible(false), TOUCH_HOLD_MS);
  }, []);

  const zoomIn = () => setFontSize((s) => Math.min(s + FONT_STEP, MAX_FONT_SIZE));
  const zoomOut = () => setFontSize((s) => Math.max(s - FONT_STEP, MIN_FONT_SIZE));

  const handleCopyAll = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(content);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = content;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch { /* ignore */ }
      document.body.removeChild(ta);
    }
  }, [content]);

  return (
    <div
      className={`fixed inset-0 z-50 flex flex-col reading-mode ${isDark ? "dark" : ""}`}
      onTouchStart={handleTouchStart}
      onMouseMove={handleMouseMove}
      onMouseLeave={() => setToolbarVisible(false)}
    >
      {/* Floating toolbar：收起时同时移出焦点顺序，避免 Tab 键摸到看不见的按钮 */}
      <div
        aria-hidden={!toolbarVisible}
        inert={!toolbarVisible}
        className={`absolute top-4 left-1/2 -translate-x-1/2 z-10 flex items-center gap-1 px-3 py-2 rounded-full bg-white/90 dark:bg-gray-800/90 backdrop-blur shadow-lg border border-gray-200 dark:border-gray-700 transition-opacity duration-300 ${
          toolbarVisible ? "opacity-100" : "opacity-0 pointer-events-none"
        }`}
      >
        {/* Font size controls */}
        <button
          onClick={zoomOut}
          disabled={fontSize <= MIN_FONT_SIZE}
          className="w-8 h-8 flex items-center justify-center rounded-full text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-30 transition-colors cursor-pointer touch-target"
          title="缩小字体"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
          </svg>
        </button>
        <span className="text-xs text-gray-500 dark:text-gray-400 min-w-[3ch] text-center tabular-nums">
          {fontSize}
        </span>
        <button
          onClick={zoomIn}
          disabled={fontSize >= MAX_FONT_SIZE}
          className="w-8 h-8 flex items-center justify-center rounded-full text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-30 transition-colors cursor-pointer touch-target"
          title="放大字体"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
        </button>

        <div className="w-px h-5 bg-gray-200 dark:bg-gray-600 mx-1" />

        {/* Theme toggle */}
        <button
          onClick={() => setIsDark((d) => !d)}
          className="w-8 h-8 flex items-center justify-center rounded-full text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors cursor-pointer touch-target"
          title={isDark ? "切换亮色" : "切换暗色"}
        >
          {isDark ? (
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
            </svg>
          ) : (
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
            </svg>
          )}
        </button>

        {/* Copy all */}
        <button
          onClick={handleCopyAll}
          className="w-8 h-8 flex items-center justify-center rounded-full text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors cursor-pointer touch-target"
          title="复制全文"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
          </svg>
        </button>

        <div className="w-px h-5 bg-gray-200 dark:bg-gray-600 mx-1" />

        {/* Close */}
        <button
          onClick={onClose}
          className="w-8 h-8 flex items-center justify-center rounded-full text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors cursor-pointer touch-target"
          title="退出阅读模式"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Content area */}
      <div className="flex-1 overflow-auto px-4 py-16">
        <div className="max-w-3xl mx-auto" style={{ fontSize: `${fontSize}px` }}>
          <MarkdownRenderer content={content} />
        </div>
      </div>
    </div>
  );
};
