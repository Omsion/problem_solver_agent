import { useState, useCallback } from "react";
import type { ComponentPropsWithoutRef } from "react";

/**
 * Custom code renderer for react-markdown.
 * - Fenced code blocks (```lang) get a header with language label + copy button.
 * - Inline code (`code`) is rendered as a plain <code> element.
 */
export function CodeBlock({
  className,
  children,
  ...props
}: ComponentPropsWithoutRef<"code">) {
  const match = /language-(\w+)/.exec(className || "");

  if (!match) {
    // Inline code — no language class
    return <code className={className} {...props}>{children}</code>;
  }

  return <CodeBlockWithCopy lang={match[1]} code={String(children).replace(/\n$/, "")} />;
}

function CodeBlockWithCopy({ lang, code }: { lang: string; code: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback for older browsers / non-HTTPS
      const ta = document.createElement("textarea");
      ta.value = code;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch { /* ignore */ }
      document.body.removeChild(ta);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }, [code]);

  return (
    <div className="code-block-wrapper relative group">
      {/* Header bar */}
      <div className="flex items-center justify-between px-4 py-1.5 bg-slate-700 rounded-t-lg text-xs">
        <span className="text-slate-300 font-mono">{lang}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 text-slate-400 hover:text-white transition-colors cursor-pointer touch-target"
        >
          {copied ? (
            <>
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              <span>已复制</span>
            </>
          ) : (
            <>
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
              <span>复制</span>
            </>
          )}
        </button>
      </div>
      {/* Code content */}
      <pre className="!mt-0 !rounded-t-none">
        <code className={languageClass(lang)}>{code}</code>
      </pre>
    </div>
  );
}

/** Map common language tags to the className react-markdown expects for syntax highlighting. */
function languageClass(lang: string): string {
  return `language-${lang}`;
}
