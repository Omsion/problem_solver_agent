import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { updateUserBudget, updateUserRole } from "../lib/api";
import { useAdminDashboard, useAdminUsers } from "../lib/queries";
import { useIsMobile } from "../hooks/useMediaQuery";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { useConfirm } from "../components/ui/confirm";
import { notify } from "../components/ui/toast";
import type { AdminTopUser, AuthUser, UserRole } from "../types";

/**
 * 管理员看板。
 *
 * 权限本身由 `RequireAdmin`（路由层）把关，这里只负责展示：
 * - 顶部三张概览卡（总用户 / 总调用 / 总花费）
 * - 用户列表：改额度（输入框 + 保存）与改角色（下拉）
 * - 移动端(<768px)换成卡片列表——七个字段的宽表格在手机上必然横向溢出
 *
 * 调用次数不在用户对象里（后端只按用户聚合花费与额度），但 `/admin/dashboard`
 * 的 `top_users` 会带去全局口径的 `calls`，这里用它补全表格中的"调用数"列。
 */
const AdminPage = () => {
  const isMobile = useIsMobile();
  const queryClient = useQueryClient();
  const confirm = useConfirm();

  const dashboardQuery = useAdminDashboard();
  const usersQuery = useAdminUsers(0, 50);
  const [busyId, setBusyId] = useState<string | null>(null);
  /** 每个用户正在编辑中的额度草稿；未编辑时回落到后端值 */
  const [budgetDrafts, setBudgetDrafts] = useState<Record<string, string>>({});

  const dashboard = dashboardQuery.data;
  const users = useMemo(() => usersQuery.data?.data ?? [], [usersQuery.data]);

  /** 用户 id → 调用次数（来自看板的 top_users） */
  const callsByUser = useMemo(() => {
    const map = new Map<string, number>();
    (dashboard?.top_users ?? []).forEach((u: AdminTopUser) => map.set(u.id, u.calls));
    return map;
  }, [dashboard]);

  const error = dashboardQuery.error ?? usersQuery.error;
  const errorText = error instanceof Error ? error.message : error ? "加载失败，请重试" : null;
  const loading = dashboardQuery.isLoading || usersQuery.isLoading;

  const refreshAll = () => {
    void dashboardQuery.refetch();
    void usersQuery.refetch();
  };

  const handleSaveBudget = async (user: AuthUser) => {
    const raw = budgetDrafts[user.id];
    if (raw === undefined) return;
    const value = Number(raw);
    if (!Number.isFinite(value) || value < 0) {
      notify.error("额度不合法", "请输入大于等于 0 的数字");
      return;
    }
    if (value === user.budget) {
      notify.info("额度未变化");
      return;
    }

    setBusyId(user.id);
    try {
      await updateUserBudget(user.id, value);
      notify.success("额度已更新", `${user.phone} → ¥${value.toFixed(2)}`);
      setBudgetDrafts((prev) => {
        const next = { ...prev };
        delete next[user.id];
        return next;
      });
      await queryClient.invalidateQueries({ queryKey: ["admin"] });
    } catch (err) {
      notify.error("更新额度失败", err instanceof Error ? err.message : undefined);
    } finally {
      setBusyId(null);
    }
  };

  const handleChangeRole = async (user: AuthUser, role: UserRole) => {
    if (role === user.role) return;

    // 提权是不可逆的敏感操作，用现有确认框拦一道
    if (role === "admin") {
      const ok = await confirm({
        title: "提升为管理员？",
        description: `${user.phone} 将能看到全部用户、用量流水，并可修改其他用户的额度与角色。`,
        confirmLabel: "提升",
      });
      if (!ok) return;
    }

    setBusyId(user.id);
    try {
      await updateUserRole(user.id, role);
      notify.success("角色已更新", `${user.phone} → ${role === "admin" ? "管理员" : "普通用户"}`);
      await queryClient.invalidateQueries({ queryKey: ["admin"] });
    } catch (err) {
      notify.error("更新角色失败", err instanceof Error ? err.message : undefined);
    } finally {
      setBusyId(null);
    }
  };

  const budgetValue = (user: AuthUser) => budgetDrafts[user.id] ?? String(user.budget);

  return (
    <div className="h-[calc(100dvh-4rem)] overflow-auto bg-gray-50">
      <div className="max-w-5xl mx-auto p-4 sm:p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-gray-900">管理看板</h2>
          <Button variant="secondary" size="sm" onClick={refreshAll} disabled={loading}>
            {loading ? "加载中…" : "刷新"}
          </Button>
        </div>

        {errorText && (
          <div className="px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-xs text-red-600">
            {errorText}
          </div>
        )}

        {/* 概览 */}
        <div className="grid grid-cols-3 gap-3">
          <StatCard label="总用户" value={dashboard ? String(dashboard.total_users) : "--"} />
          <StatCard label="总调用" value={dashboard ? String(dashboard.calls) : "--"} />
          <StatCard label="总花费" value={dashboard ? `¥${dashboard.cost.toFixed(4)}` : "--"} />
        </div>

        {dashboard && (
          <p className="text-xs text-gray-400">
            输入 {(dashboard.input_tokens ?? 0).toLocaleString()} tokens · 输出{" "}
            {(dashboard.output_tokens ?? 0).toLocaleString()} tokens
          </p>
        )}

        {/* 用户列表 */}
        <Card>
          <CardHeader className="flex items-center justify-between">
            <CardTitle>用户（{usersQuery.data?.total ?? users.length}）</CardTitle>
          </CardHeader>
          <CardContent className={isMobile ? "p-3 space-y-3" : "p-0"}>
            {usersQuery.isLoading && users.length === 0 && (
              <div className="space-y-2 p-3">
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="h-12 bg-gray-100 rounded-lg animate-pulse" />
                ))}
              </div>
            )}

            {!usersQuery.isLoading && users.length === 0 && !errorText && (
              <p className="px-3 py-6 text-center text-xs text-gray-400">暂无用户</p>
            )}

            {isMobile
              ? users.map((user) => (
                  <UserCard
                    key={user.id}
                    user={user}
                    calls={callsByUser.get(user.id)}
                    budgetValue={budgetValue(user)}
                    busy={busyId === user.id}
                    onBudgetChange={(v) => setBudgetDrafts((prev) => ({ ...prev, [user.id]: v }))}
                    onSaveBudget={() => void handleSaveBudget(user)}
                    onRoleChange={(role) => void handleChangeRole(user, role)}
                  />
                ))
              : users.length > 0 && (
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="text-gray-400 border-b border-gray-100">
                          <th className="text-left font-normal px-4 py-3">手机号</th>
                          <th className="text-left font-normal px-3 py-3">角色</th>
                          <th className="text-right font-normal px-3 py-3">额度</th>
                          <th className="text-right font-normal px-3 py-3">已用</th>
                          <th className="text-right font-normal px-3 py-3">剩余</th>
                          <th className="text-right font-normal px-3 py-3">调用</th>
                          <th className="text-right font-normal px-4 py-3">调整额度</th>
                        </tr>
                      </thead>
                      <tbody>
                        {users.map((user) => (
                          <tr key={user.id} className="border-b border-gray-50 last:border-0">
                            <td className="px-4 py-2.5 text-gray-700 whitespace-nowrap">{user.phone}</td>
                            <td className="px-3 py-2.5">
                              <RoleSelect
                                user={user}
                                busy={busyId === user.id}
                                onChange={(role) => void handleChangeRole(user, role)}
                              />
                            </td>
                            <td className="px-3 py-2.5 text-right text-gray-700 tabular-nums">
                              ¥{user.budget.toFixed(2)}
                            </td>
                            <td className="px-3 py-2.5 text-right text-gray-700 tabular-nums">
                              ¥{user.spent.toFixed(2)}
                            </td>
                            <td
                              className={`px-3 py-2.5 text-right tabular-nums ${user.remaining <= 0 ? "text-red-500" : "text-gray-700"}`}
                            >
                              ¥{user.remaining.toFixed(2)}
                            </td>
                            <td className="px-3 py-2.5 text-right text-gray-500 tabular-nums">
                              {callsByUser.get(user.id) ?? "—"}
                            </td>
                            <td className="px-4 py-2.5">
                              <BudgetEditor
                                value={budgetValue(user)}
                                busy={busyId === user.id}
                                onChange={(v) => setBudgetDrafts((prev) => ({ ...prev, [user.id]: v }))}
                                onSave={() => void handleSaveBudget(user)}
                              />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
          </CardContent>
        </Card>

        {/* Top 用户 */}
        {dashboard && dashboard.top_users.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>花费 Top 用户</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <ul className="divide-y divide-gray-50">
                {dashboard.top_users.slice(0, 10).map((u) => (
                  <li key={u.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-xs">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="text-gray-700 truncate">{u.phone}</span>
                      {u.role === "admin" && <Badge variant="info">管理员</Badge>}
                    </div>
                    <div className="flex items-center gap-4 shrink-0 text-gray-500 tabular-nums">
                      <span>{u.calls} 次</span>
                      <span className="text-gray-700">¥{u.cost.toFixed(4)}</span>
                    </div>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
};

const StatCard = ({ label, value }: { label: string; value: string }) => (
  <Card>
    <CardContent className="p-4">
      <p className="text-xs text-gray-400">{label}</p>
      <p className="mt-1 text-lg sm:text-xl font-semibold text-gray-900 tabular-nums">{value}</p>
    </CardContent>
  </Card>
);

const RoleSelect = ({
  user,
  busy,
  onChange,
}: {
  user: AuthUser;
  busy: boolean;
  onChange: (role: UserRole) => void;
}) => (
  <select
    value={user.role}
    disabled={busy}
    onChange={(e) => onChange(e.target.value as UserRole)}
    className="h-8 rounded-md border border-gray-300 bg-white px-2 text-xs text-gray-700 cursor-pointer disabled:opacity-50"
    aria-label={`${user.phone} 的角色`}
  >
    <option value="user">普通用户</option>
    <option value="admin">管理员</option>
  </select>
);

const BudgetEditor = ({
  value,
  busy,
  onChange,
  onSave,
}: {
  value: string;
  busy: boolean;
  onChange: (value: string) => void;
  onSave: () => void;
}) => (
  <div className="flex items-center justify-end gap-1.5">
    <input
      type="number"
      min={0}
      step="0.01"
      inputMode="decimal"
      value={value}
      disabled={busy}
      onChange={(e) => onChange(e.target.value)}
      className="w-20 h-8 px-2 text-xs text-right rounded-md border border-gray-300 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 focus:outline-none tabular-nums"
    />
    <Button size="sm" variant="secondary" disabled={busy} onClick={onSave}>
      保存
    </Button>
  </div>
);

/** 移动端单个用户卡片：把宽表格的每一行拆成"标签 + 值"多行 */
const UserCard = ({
  user,
  calls,
  budgetValue,
  busy,
  onBudgetChange,
  onSaveBudget,
  onRoleChange,
}: {
  user: AuthUser;
  calls: number | undefined;
  budgetValue: string;
  busy: boolean;
  onBudgetChange: (value: string) => void;
  onSaveBudget: () => void;
  onRoleChange: (role: UserRole) => void;
}) => (
  <div className="rounded-xl border border-gray-200 p-3 space-y-2">
    <div className="flex items-center justify-between gap-2">
      <span className="text-sm font-medium text-gray-800">{user.phone}</span>
      <RoleSelect user={user} busy={busy} onChange={onRoleChange} />
    </div>
    <div className="grid grid-cols-3 gap-2 text-xs">
      <Cell label="额度" value={`¥${user.budget.toFixed(2)}`} />
      <Cell label="已用" value={`¥${user.spent.toFixed(2)}`} />
      <Cell label="剩余" value={`¥${user.remaining.toFixed(2)}`} danger={user.remaining <= 0} />
    </div>
    <div className="flex items-center justify-between gap-3">
      <span className="text-xs text-gray-400">调用 {calls ?? "—"} 次</span>
      <BudgetEditor value={budgetValue} busy={busy} onChange={onBudgetChange} onSave={onSaveBudget} />
    </div>
  </div>
);

const Cell = ({ label, value, danger }: { label: string; value: string; danger?: boolean }) => (
  <div>
    <p className="text-gray-400">{label}</p>
    <p className={`tabular-nums ${danger ? "text-red-500" : "text-gray-700"}`}>{value}</p>
  </div>
);

export default AdminPage;
