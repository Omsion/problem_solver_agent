import { useEffect, useState } from "react";
import { getSystemStatus, getHealth, getStats } from "../../lib/api";
import { QrCodeButton } from "../layout/QrCodeButton";
import { Button } from "../ui/button";
import { formatDuration } from "../../lib/utils";
import { formatTs } from "../../lib/utils";
import type { StageStats, SystemStatus } from "../../types";

interface HealthInfo {
  status: string;
  version: string;
  vision_configured: boolean;
  solver_providers: string[];
  keys_configured: Record<string, boolean>;
}

/**
 * 设置页。
 *
 * 缺陷背景：自动截图监控是否在跑，之前界面上完全看不出来——按了热键没反应时
 * 用户无从判断是监控没启动还是截图没落盘。这里把 `/api/status`、`/api/health`、
 * `/api/stats` 三个端点集中展示出来。
 */
export const SettingsPage = () => {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [stats, setStats] = useState<Record<string, StageStats> | null>(null);
  const [cacheHitRate, setCacheHitRate] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusData, healthData, statsData] = await Promise.all([
        getSystemStatus(),
        getHealth(),
        getStats(),
      ]);
      setStatus(statusData);
      setHealth(healthData);
      setStats(statsData.stages);
      setCacheHitRate(statsData.cache_hit_rate);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载设置失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 10000);
    return () => clearInterval(timer);
  }, []);

  const mb = (bytes: number | undefined) =>
    bytes === undefined ? "--" : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

  return (
    <div className="h-[calc(100dvh-3.5rem)] overflow-auto bg-gray-50">
      <div className="max-w-3xl mx-auto p-4 sm:p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-gray-900">设置与运行状态</h2>
          <Button variant="secondary" size="sm" onClick={() => void load()} disabled={loading}>
            刷新
          </Button>
        </div>

        {error && (
          <div className="px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-xs text-red-600">{error}</div>
        )}

        {/* 自动截图监控 */}
        <section className="bg-white rounded-xl border border-gray-200 p-4">
          <h3 className="text-sm font-semibold text-gray-800 mb-3">自动截图监控</h3>
          <div className="space-y-2 text-sm">
            <Row label="状态">
              {status?.running ? (
                <span className="flex items-center gap-1.5 text-green-600">
                  <span className="w-2 h-2 rounded-full bg-green-500" /> 监控中
                </span>
              ) : status?.auto_import_enabled ? (
                <span className="text-amber-600">已启用但未运行</span>
              ) : (
                <span className="text-gray-500">已禁用</span>
              )}
            </Row>
            <Row label="监控目录">
              <code className="text-xs text-gray-600 break-all">{status?.monitor_dir || "--"}</code>
            </Row>
            <Row label="最近一次截图组">{status?.last_group_at ? formatTs(status.last_group_at) : "暂无"}</Row>
            <Row label="累计处理组数">{status?.groups_handled ?? 0}</Row>
            <Row label="处理中">{status?.processing ?? 0}</Row>
            {!status?.running && status?.auto_import_enabled === false && (
              <p className="text-xs text-amber-600 pt-1">
                自动导入已被环境变量 <code>AUTO_IMPORT_ENABLED=false</code> 关闭。想用热键截图自动出答案，
                请去掉该变量后重启。
              </p>
            )}
          </div>
        </section>

        {/* 磁盘占用 */}
        <section className="bg-white rounded-xl border border-gray-200 p-4">
          <h3 className="text-sm font-semibold text-gray-800 mb-3">磁盘占用</h3>
          <div className="space-y-2 text-sm">
            <Row label="上传原图">{mb(status?.uploads_bytes)}</Row>
            <Row label="解答文件">{mb(status?.solutions_bytes)}</Row>
          </div>
          <p className="text-xs text-gray-400 mt-2">
            超出保留数量或天数的任务会自动清理（解答文件与上传目录一并删除）。
          </p>
        </section>

        {/* 模型与密钥 */}
        <section className="bg-white rounded-xl border border-gray-200 p-4">
          <h3 className="text-sm font-semibold text-gray-800 mb-3">模型与密钥</h3>
          <div className="space-y-2 text-sm">
            <Row label="视觉模型">
              {health?.vision_configured ? (
                <span className="text-green-600">已配置</span>
              ) : (
                <span className="text-red-500">缺少 ZHIPU_API_KEY</span>
              )}
            </Row>
            <Row label="求解器">
              <span className="text-gray-600">{(health?.solver_providers ?? []).join(", ") || "--"}</span>
            </Row>
            <Row label="密钥状态">
              <span className="text-xs text-gray-500">
                {Object.entries(health?.keys_configured ?? {})
                  .map(([provider, ok]) => `${provider}:${ok ? "✓" : "✗"}`)
                  .join("  ") || "--"}
              </span>
            </Row>
            <Row label="后端版本">{health?.version ?? "--"}</Row>
          </div>
        </section>

        {/* 阶段耗时 */}
        <section className="bg-white rounded-xl border border-gray-200 p-4">
          <h3 className="text-sm font-semibold text-gray-800 mb-3">阶段耗时（最近任务）</h3>
          {stats ? (
            <div className="space-y-2">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-gray-400">
                    <th className="text-left font-normal pb-1">阶段</th>
                    <th className="text-right font-normal pb-1">中位数</th>
                    <th className="text-right font-normal pb-1">P90</th>
                    <th className="text-right font-normal pb-1">样本</th>
                    <th className="text-right font-normal pb-1">缓存命中</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(stats).map(([name, stage]) => (
                    <tr key={name} className="border-t border-gray-100">
                      <td className="py-1 text-gray-600">{STAGE_LABELS[name] ?? name}</td>
                      <td className="py-1 text-right tabular-nums text-gray-700">{formatDuration(stage.p50)}</td>
                      <td className="py-1 text-right tabular-nums text-gray-500">{formatDuration(stage.p90)}</td>
                      <td className="py-1 text-right tabular-nums text-gray-500">{stage.samples}</td>
                      <td className="py-1 text-right tabular-nums text-gray-500">{stage.cache_hits}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="text-xs text-gray-400">缓存命中率 {(cacheHitRate * 100).toFixed(0)}%</p>
            </div>
          ) : (
            <p className="text-xs text-gray-400">暂无数据</p>
          )}
        </section>

        {/* 手机访问 */}
        <section className="bg-white rounded-xl border border-gray-200 p-4">
          <h3 className="text-sm font-semibold text-gray-800 mb-3">手机访问</h3>
          <div className="flex items-center gap-3">
            <QrCodeButton forceShow />
            <span className="text-xs text-gray-500">
              手机与电脑在同一局域网时，扫码即可在手机上查看解答
            </span>
          </div>
          {status?.lan_ip && (
            <p className="text-xs text-gray-400 mt-2">
              局域网地址：<code>http://{status.lan_ip}:8000</code>
            </p>
          )}
        </section>
      </div>
    </div>
  );
};

const STAGE_LABELS: Record<string, string> = {
  classify: "问题分类",
  ocr: "文字识别",
  polish: "文本润色",
  solve: "AI 求解",
  total: "总耗时",
};

const Row = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div className="flex items-start justify-between gap-4">
    <span className="text-gray-500 shrink-0">{label}</span>
    <span className="text-right min-w-0">{children}</span>
  </div>
);
