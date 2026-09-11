import { useState } from "react";
import { Check, Copy, ShieldCheck, TriangleAlert, CircleHelp, ChevronDown } from "lucide-react";
import type { AnswerCard as AnswerCardData, VerificationResult } from "../../types";
import { cn } from "../../lib/utils";

interface Props {
  card: AnswerCardData;
  verification: VerificationResult | null;
  verifying: boolean;
  onVerify: () => void;
  onShowFull: () => void;
  canVerify: boolean;
}

/**
 * 答案卡 —— 考试场景下真正要"抄走"的东西。
 *
 * 背景：之前解答是整篇 markdown 直接铺在滚动区里，用户要自己滑、自己找结论。
 * 这里把后端抽取出的「最终答案」单独提成一张大字号卡片，并提供一键复制；
 * 完整推理默认折叠在下方，需要时再展开。
 *
 * 核对结果（可选功能）以状态条形式贴在卡片顶部：只有 `disagree` 才用警示色，
 * 避免一次误判把正常的答案吓成"错误"。
 */
export const AnswerCard = ({
  card,
  verification,
  verifying,
  onVerify,
  onShowFull,
  canVerify,
}: Props) => {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(card.text);
    } catch {
      // 非 HTTPS 环境下 clipboard 不可用，退回 execCommand
      const area = document.createElement("textarea");
      area.value = card.text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy");
      } catch {
        /* ignore */
      }
      document.body.removeChild(area);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section className="rounded-2xl border border-indigo-100 bg-indigo-50/40 overflow-hidden">
      {verification && <VerificationBar verification={verification} />}

      <div className="px-4 py-3 sm:px-5 sm:py-4">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs font-medium text-indigo-500">
            {verification ? "核对后答案" : "最终答案"}
          </span>
          {!card.extracted && (
            <span className="text-[11px] text-gray-400" title="解答中没有明确的「最终答案」小节，这里展示的是首段内容">
              自动提取
            </span>
          )}
          {card.truncated && <span className="text-[11px] text-amber-600">已截断</span>}
        </div>

        <div className="max-h-[45vh] overflow-auto">
          <p className="whitespace-pre-wrap break-words text-base sm:text-lg font-medium leading-relaxed text-gray-900">
            {card.text}
          </p>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            onClick={copy}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors cursor-pointer",
              copied ? "bg-green-600 text-white" : "bg-indigo-600 text-white hover:bg-indigo-700",
            )}
          >
            {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
            {copied ? "已复制" : "复制答案"}
          </button>

          <button
            onClick={onShowFull}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50 transition-colors cursor-pointer"
          >
            <ChevronDown className="w-4 h-4" />
            完整解答
          </button>

          {canVerify && (
            <button
              onClick={onVerify}
              disabled={verifying}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors cursor-pointer"
              title="用第二个视觉模型对照原图复核答案"
            >
              <ShieldCheck className="w-4 h-4" />
              {verifying ? "核对中…" : verification ? "重新核对" : "核对答案"}
            </button>
          )}
        </div>
      </div>
    </section>
  );
};

const VerificationBar = ({ verification }: { verification: VerificationResult }) => {
  const tone = {
    agree: {
      wrap: "bg-green-50 border-green-200 text-green-800",
      icon: <Check className="w-4 h-4" />,
      label: "核对通过",
    },
    disagree: {
      wrap: "bg-amber-50 border-amber-200 text-amber-800",
      icon: <TriangleAlert className="w-4 h-4" />,
      label: "核对发现疑点",
    },
    unclear: {
      wrap: "bg-gray-50 border-gray-200 text-gray-600",
      icon: <CircleHelp className="w-4 h-4" />,
      label: "无法判定",
    },
  }[verification.verdict];

  return (
    <div className={cn("border-b px-4 py-2.5 sm:px-5", tone.wrap)}>
      <div className="flex items-center gap-2 text-sm font-medium">
        {tone.icon}
        {tone.label}
        {verification.model && (
          <span className="ml-auto text-[11px] font-normal opacity-70">{verification.model}</span>
        )}
      </div>

      {verification.issues.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs list-disc pl-5">
          {verification.issues.map((issue, index) => (
            <li key={index}>{issue}</li>
          ))}
        </ul>
      )}

      {verification.corrections && (
        <div className="mt-2 rounded-lg bg-white/70 px-3 py-2 text-xs whitespace-pre-wrap break-words">
          <span className="font-medium">建议修正：</span>
          {verification.corrections}
        </div>
      )}

      {verification.reason && !verification.corrections && (
        <p className="mt-1.5 text-xs opacity-80">{verification.reason}</p>
      )}
    </div>
  );
};
