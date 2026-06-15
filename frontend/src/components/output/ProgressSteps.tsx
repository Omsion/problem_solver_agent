import type { ProgressState } from "../../types";
import { useIsMobile } from "../../hooks/useMediaQuery";

interface Props {
  phase: ProgressState["phase"];
  message: string;
}

const steps = [
  { key: "classifying", label: "问题分类" },
  { key: "ocr", label: "文字识别" },
  { key: "solving", label: "AI 求解" },
  { key: "done", label: "完成" },
] as const;

const phaseOrder: Record<string, number> = {
  idle: -1,
  classifying: 0,
  ocr: 1,
  solving: 2,
  done: 3,
  error: -2,
};

export const ProgressSteps = ({ phase, message }: Props) => {
  const currentIdx = phaseOrder[phase] ?? -1;
  const isError = phase === "error";
  const isMobile = useIsMobile();

  const circleSize = isMobile ? "w-6 h-6" : "w-8 h-8";
  const lineWidth = isMobile ? "w-6" : "w-8";
  const textSize = isMobile ? "text-[10px]" : "text-xs";

  return (
    <div className="py-2 sm:py-4">
      <div className="flex items-center justify-center gap-0">
        {steps.map((step, i) => {
          const isCompleted = currentIdx > i;
          const isActive = currentIdx === i;

          return (
            <div key={step.key} className="flex items-center">
              {/* Step circle */}
              <div className="flex flex-col items-center">
                <div
                  className={`${circleSize} rounded-full flex items-center justify-center ${textSize} font-semibold transition-colors ${
                    isError
                      ? "bg-red-100 text-red-600"
                      : isCompleted
                        ? "bg-indigo-600 text-white"
                        : isActive
                          ? "bg-indigo-100 text-indigo-600 ring-2 ring-indigo-300"
                          : "bg-gray-100 text-gray-400"
                  }`}
                >
                  {isCompleted ? (
                    <svg className={`${isMobile ? "w-3 h-3" : "w-4 h-4"}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                    </svg>
                  ) : isActive ? (
                    <span className="animate-pulse">&#9679;</span>
                  ) : (
                    i + 1
                  )}
                </div>
                <span className={`${textSize} mt-1 ${isActive ? "text-indigo-600 font-medium" : "text-gray-400"}`}>
                  {step.label}
                </span>
              </div>
              {/* Connecting line */}
              {i < steps.length - 1 && (
                <div
                  className={`${lineWidth} h-0.5 mx-0.5 sm:mx-1 mb-5 transition-colors ${
                    isCompleted ? "bg-indigo-400" : "bg-gray-200"
                  }`}
                />
              )}
            </div>
          );
        })}
      </div>
      {message && (
        <p className={`text-center text-sm mt-2 sm:mt-3 ${isError ? "text-red-500" : "text-gray-500"}`}>
          {message}
        </p>
      )}
    </div>
  );
};
