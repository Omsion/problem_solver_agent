import { useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { rotateApiKey } from "../lib/api";
import { useAuth, useMe } from "../lib/queries";
import { ME_QUERY_KEY } from "../hooks/useAuth";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Dialog } from "../components/ui/dialog";
import { useConfirm } from "../components/ui/confirm";
import { notify } from "../components/ui/toast";
import { formatTs } from "../lib/utils";
import type { UsageByModel, UsageByStage } from "../types";

/**
 * 用量与额度页。
 *
 * 数据只有一个来源 `GET /api/v1/auth/me`，走 TanStack Query 缓存：
 * 路由守卫与顶栏用的也是同一份 `["auth","me"]`，所以这里看到的就是全站口径，
 * 不会出现"顶栏显示剩余 3 元、本页显示剩余 5 元"的分裂。
 *
 * 本地模式（`auth_enabled=false`）下后端同样会返回内置本地用户与用量，
 * 因此本页不需要登录也能正常展示。
 */

const UsagePage = () => {
  const { data, isLoading, error, refetch } = useMe();
  const { authEnabled } = useAuth();
  const confirm = useConfirm();
  const queryClient = useQueryClient();

  const user = data?.user;
  const usage = data?.usage;

  const [rotating, setRotating] = useState(false);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const handleRotate = async () => {
    const ok = await confirm({
      title: "重新生成 API Key？",
      description: "旧 Key 会立即失效，任何正在使用它的脚本或服务都需要同步更新。",
      confirmLabel: "重新生成",
      destructive: true,
    });
    if (!ok) return;

    setRotating(true);
    try {
      const res = await rotateApiKey();
      setNewKey(res.api_key);
      setCopied(false);
      notify.success("已生成新的 API Key", "请立即保存，关闭后无法再次查看");
      // 掩码变了必须回源刷新：本页正订阅着 ["auth","me"]，invalidate 会立刻重取，
      // 保证卡片上的掩码与后端一致。
      await queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY });
    } catch (err) {
      notify.error("生成失败", err instanceof Error ? err.message : undefined);
    } finally {
      setRotating(false);
    }
  };

  const handleCopy = async () => {
    if (!newKey) return;
    try {
      await navigator.clipboard.writeText(newKey);
      setCopied(true);
      notify.success("已复制到剪贴板");
    } catch {
      // 非 HTTPS 或浏览器禁止剪贴板时给出可操作的提示，而不是静默失败
      notify.error("复制失败", "请长按选中密钥手动复制");
    }
  };

  const errorText = error instanceof Error ? error.message : error ? "加载失败" : null;

  const budget = user?.budget ?? 0;
  const spent = user?.spent ?? 0;
  const remaining = user?.remaining ?? 0;
  // 额度为 0 时百分比无意义（会除零），按 0 处理并把进度条标成中性色
  const ratio = budget > 0 ? Math.min(1, spent / budget) : 0;
  const overBudget = budget > 0 && spent > budget;

  return (
    <div className="h-[calc(100dvh-4rem)] overflow-auto bg-gray-50">
      <div className="max-w-3xl mx-auto p-4 sm:p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold text-gray-900">额度与用量</h2>
            {authEnabled === false && <Badge variant="info">本地模式</Badge>}
          </div>
          <Button variant="secondary" size="sm" onClick={() => void refetch()} disabled={isLoading}>
            {isLoading ? "刷新中…" : "刷新"}
          </Button>
        </div>

        {errorText && (
          <div className="px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-xs text-red-600">
            {errorText}
          </div>
        )}

        {/* 额度 */}
        <Card>
          <CardHeader>
            <CardTitle>额度</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-end justify-between gap-3">
              <div>
                <p className="text-xs text-gray-400">剩余额度</p>
                <p className={`text-2xl font-semibold tabular-nums ${remaining <= 0 ? "text-red-500" : "text-gray-900"}`}>
                  ¥{remaining.toFixed(2)}
                </p>
              </div>
              <div className="text-right text-xs text-gray-500 tabular-nums">
                <p>总额度 ¥{budget.toFixed(2)}</p>
                <p>已用 ¥{spent.toFixed(2)}</p>
              </div>
            </div>
            <div className="h-2 w-full rounded-full bg-gray-100 overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${overBudget || remaining <= 0 ? "bg-red-500" : "bg-indigo-500"}`}
                style={{ width: `${Math.round(ratio * 100)}%` }}
              />
            </div>
            <p className="text-xs text-gray-400">
              已使用 {budget > 0 ? `${(ratio * 100).toFixed(1)}%` : "—"}；额度不足时新任务会被拒绝。
            </p>
            {user && (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-1 text-xs text-gray-500">
                <span>
                  账号：<span className="text-gray-700">{user.phone}</span>
                </span>
                <span>
                  角色：
                  <span className="text-gray-700">{user.role === "admin" ? "管理员" : "普通用户"}</span>
                </span>
                <span>最近登录：{formatTs(user.last_login_at)}</span>
              </div>
            )}
          </CardContent>
        </Card>

        {/* 汇总 */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <StatCard label="总调用次数" value={String(usage?.calls ?? 0)} />
          <StatCard label="总花费" value={`¥${(usage?.cost ?? 0).toFixed(4)}`} />
          <StatCard label="输入 tokens" value={(usage?.input_tokens ?? 0).toLocaleString()} />
          <StatCard label="输出 tokens" value={(usage?.output_tokens ?? 0).toLocaleString()} />
        </div>

        {/* 按模型 / 按阶段 */}
        <div className="grid gap-4 sm:grid-cols-2">
          <BreakdownTable
            title="按模型"
            emptyText="暂无模型调用记录"
            rows={(usage?.by_model ?? []).map((m: UsageByModel) => ({
              key: `model-${m.model}`,
              label: m.model || "未知模型",
              calls: m.calls,
              cost: m.cost,
            }))}
          />
          <BreakdownTable
            title="按阶段"
            emptyText="暂无阶段用量记录"
            rows={(usage?.by_stage ?? []).map((s: UsageByStage) => ({
              key: `stage-${s.stage}`,
              // 后端的 stage 是内部标识（classify/ocr/solve…），这里换成中文
              label: STAGE_LABELS[s.stage] ?? (s.stage || "未知阶段"),
              calls: s.calls,
              cost: s.cost,
            }))}
          />
        </div>

        {/* API Key */}
        <Card>
          <CardHeader>
            <CardTitle>API Key</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center gap-3">
              <code className="flex-1 min-w-0 text-xs text-gray-700 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 truncate font-mono">
                {user?.api_key_masked || "未生成"}
              </code>
              <Button variant="secondary" size="sm" onClick={() => void handleRotate()} disabled={rotating}>
                {rotating ? "生成中…" : "重新生成"}
              </Button>
            </div>
            <p className="text-xs text-gray-400">
              重新生成后旧 Key 立即失效，完整 Key 只在生成时显示一次。
            </p>
          </CardContent>
        </Card>

        <p className="text-center text-xs text-gray-400 pb-2">
          <Link to="/settings" className="text-indigo-500 hover:text-indigo-600">
            前往设置
          </Link>
          <span className="mx-2">·</span>
          <Link to="/" className="text-indigo-500 hover:text-indigo-600">
            返回解题台
          </Link>
        </p>
      </div>

      <Dialog
        open={newKey !== null}
        onClose={() => setNewKey(null)}
        title="新的 API Key"
        className="w-[92vw] max-w-md p-5"
      >
        <h3 className="text-base font-semibold text-gray-900">新的 API Key</h3>
        <p className="mt-2 text-xs text-amber-600">
          只显示一次：关闭本窗口后无法再次查看，请立即复制保存。
        </p>
        <code className="mt-3 block w-full break-all rounded-lg bg-gray-50 border border-gray-200 p-3 text-xs font-mono text-gray-800">
          {newKey}
        </code>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={() => void handleCopy()}>
            {copied ? "已复制" : "复制"}
          </Button>
          <Button size="sm" onClick={() => setNewKey(null)}>
            我已保存
          </Button>
        </div>
      </Dialog>
    </div>
  );
};

const STAGE_LABELS: Record<string, string> = {
  classify: "问题分类",
  ocr: "文字识别",
  polish: "文本润色",
  solve: "AI 求解",
  verify: "核对答案",
  total: "总计",
};

const StatCard = ({ label, value }: { label: string; value: string }) => (
  <Card>
    <CardContent className="p-4">
      <p className="text-xs text-gray-400">{label}</p>
      <p className="mt-1 text-lg font-semibold text-gray-900 tabular-nums">{value}</p>
    </CardContent>
  </Card>
);

interface BreakdownRow {
  key: string;
  label: string;
  calls: number;
  cost: number;
}

const BreakdownTable = ({ title, rows, emptyText }: { title: string; rows: BreakdownRow[]; emptyText: string }) => (
  <Card>
    <CardHeader>
      <CardTitle>{title}</CardTitle>
    </CardHeader>
    <CardContent className="p-0">
      {rows.length === 0 ? (
        <p className="px-5 py-4 text-xs text-gray-400">{emptyText}</p>
      ) : (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-400">
              <th className="text-left font-normal px-5 py-2">名称</th>
              <th className="text-right font-normal px-2 py-2">调用</th>
              <th className="text-right font-normal px-5 py-2">花费</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-t border-gray-100">
                <td className="px-5 py-2 text-gray-600 break-all">{row.label}</td>
                <td className="px-2 py-2 text-right text-gray-700 tabular-nums">{row.calls}</td>
                <td className="px-5 py-2 text-right text-gray-700 tabular-nums">¥{row.cost.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </CardContent>
  </Card>
);

export default UsagePage;
