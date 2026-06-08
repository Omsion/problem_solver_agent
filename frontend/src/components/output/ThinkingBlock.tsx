import { useState } from "react";

interface Props {
  content: string;
}

export const ThinkingBlock = ({ content }: Props) => {
  const [expanded, setExpanded] = useState(false);

  if (!content) return null;

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
        <span className="text-xs text-indigo-400 ml-auto">{content.length} 字符</span>
      </button>
      <div
        className={`transition-all duration-300 ${
          expanded ? "max-h-96 opacity-100 overflow-y-auto" : "max-h-0 opacity-0"
        }`}
      >
        <div className="p-4 bg-gray-50 text-sm text-gray-700 whitespace-pre-wrap font-mono leading-relaxed border-t border-indigo-100">
          {content}
        </div>
      </div>
    </div>
  );
};
