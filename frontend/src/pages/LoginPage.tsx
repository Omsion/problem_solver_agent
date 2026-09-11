import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, login, register, sendCode } from "../lib/api";
import { setToken } from "../lib/auth";
import { ME_QUERY_KEY, safeRedirectPath } from "../hooks/useAuth";
import { cn } from "../lib/utils";
import type { TokenResponse } from "../types";
import { Button } from "../components/ui/button";
import { notify } from "../components/ui/toast";

/**
 * 登录页。
 *
 * 面向手机使用（考生多数在手机上打开），所以：
 * - 单列窄卡片、大号触控目标、`inputMode="numeric"` 唤起数字键盘
 * - 验证码按钮自带 60 秒倒计时，倒计时期间禁用，避免被连点触发短信轰炸
 *
 * 两种方式对应后端两个端点：
 * - **验证码**：`send-code` + `register`（新手机号）。若手机号已存在，
 *   `register` 会返回 `phone_taken`，此时自动回退到"用手机号后 6 位作为初始密码
 *   登录"（与后端 `register` 里 `password or phone[-6:]` 的默认一致）。
 * - **密码**：`login`。
 */

const PHONE_PATTERN = /^1[3-9]\d{9}$/;
const COUNTDOWN_SECONDS = 60;

type Mode = "code" | "password";

const LoginPage = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();

  const [mode, setMode] = useState<Mode>("code");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [debugCode, setDebugCode] = useState<string | null>(null);
  const [countdown, setCountdown] = useState(0);
  const [sending, setSending] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);

  // 倒计时：每个数字用 setTimeout 逐秒推进，卸载时清掉，避免"登录成功跳走后还在跑"
  useEffect(() => {
    if (countdown <= 0) return;
    timerRef.current = window.setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      timerRef.current = null;
    };
  }, [countdown]);

  const from = safeRedirectPath((location.state as { from?: unknown } | null)?.from);

  /**
   * 保存令牌并让全站立即看到新身份。
   *
   * 先把 `/me` 的缓存写成登录响应里的用户：路由守卫与顶栏在跳转前就已是最新状态，
   * 否则会出现"跳回首页又被弹回登录页"的一帧闪烁。随后 invalidate 让后台再校准一次
   * （响应里的 `auth_enabled` 前端还不知道，必须回源确认）。
   */
  const completeLogin = (payload: TokenResponse) => {
    setToken(payload.access_token);
    queryClient.setQueryData(ME_QUERY_KEY, {
      user: payload.user,
      usage: null,
      // 能走到登录成功必然意味着后端已开启认证
      auth_enabled: true,
    });
    void queryClient.refetchQueries({ queryKey: ME_QUERY_KEY });
    notify.success("登录成功");
    navigate(from, { replace: true });
  };

  const validatePhone = (): string | null => {
    const trimmed = phone.trim();
    if (!PHONE_PATTERN.test(trimmed)) {
      setFieldError("请输入正确的 11 位手机号");
      return null;
    }
    setFieldError(null);
    return trimmed;
  };

  const handleSendCode = async () => {
    const valid = validatePhone();
    if (!valid) return;

    setSending(true);
    try {
      const res = await sendCode(valid);
      if (!res.ok) {
        notify.error("验证码发送失败", res.message ?? `当前短信通道：${res.provider}`);
        return;
      }
      setCountdown(COUNTDOWN_SECONDS);
      if (res.debug_code) {
        // 开发模式（SMS_PROVIDER=console）：验证码就在响应里，直接填好省去手输
        setDebugCode(res.debug_code);
        setCode(res.debug_code);
        notify.info("开发模式验证码已自动填入", "生产环境不会返回验证码");
      } else {
        setDebugCode(null);
        notify.success("验证码已发送", `${Math.round(res.expires_in / 60)} 分钟内有效`);
      }
    } catch (err) {
      notify.error("验证码发送失败", err instanceof Error ? err.message : undefined);
    } finally {
      setSending(false);
    }
  };

  const handleCodeSubmit = async (phoneValue: string) => {
    if (!code.trim()) {
      setFieldError("请输入验证码");
      return;
    }
    try {
      const res = await register({ phone: phoneValue, code: code.trim() });
      completeLogin(res);
    } catch (err) {
      // 手机号已注册：注册被拒是预期路径，改走"初始密码登录"，
      // 让验证码入口对老用户也可用，而不是只丢一句报错。
      if (err instanceof ApiError && err.code === "phone_taken") {
        try {
          const res = await login({ phone: phoneValue, password: phoneValue.slice(-6) });
          completeLogin(res);
          return;
        } catch {
          notify.error("该手机号已注册", "请切换到密码登录");
          setMode("password");
          return;
        }
      }
      notify.error("登录失败", err instanceof Error ? err.message : undefined);
    }
  };

  const handlePasswordSubmit = async (phoneValue: string) => {
    if (!password) {
      setFieldError("请输入密码");
      return;
    }
    try {
      const res = await login({ phone: phoneValue, password });
      completeLogin(res);
    } catch (err) {
      notify.error("登录失败", err instanceof Error ? err.message : undefined);
    }
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const valid = validatePhone();
    if (!valid) return;

    setSubmitting(true);
    try {
      if (mode === "code") await handleCodeSubmit(valid);
      else await handlePasswordSubmit(valid);
    } finally {
      setSubmitting(false);
    }
  };

  const switchMode = (next: Mode) => {
    setMode(next);
    setFieldError(null);
  };

  return (
    <div className="min-h-[calc(100dvh-4rem)] bg-gray-50 flex flex-col items-center justify-center px-4 py-8">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center gap-2 mb-6">
          <div className="w-12 h-12 rounded-2xl bg-indigo-600 flex items-center justify-center">
            <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
              />
            </svg>
          </div>
          <h1 className="text-lg font-bold text-gray-900">登录自动化解题 Agent</h1>
          <p className="text-xs text-gray-400">手机号登录后可查看额度与用量</p>
        </div>

        <form onSubmit={handleSubmit} className="bg-white rounded-2xl border border-gray-200 p-5 space-y-4">
          {/* 方式切换 */}
          <div className="grid grid-cols-2 gap-1 p-1 bg-gray-100 rounded-lg">
            {(
              [
                { key: "code", label: "验证码登录" },
                { key: "password", label: "密码登录" },
              ] as const
            ).map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => switchMode(item.key)}
                className={cn(
                  "py-2 text-sm font-medium rounded-md transition-colors cursor-pointer",
                  mode === item.key ? "bg-white text-indigo-600 shadow-sm" : "text-gray-500 hover:text-gray-700",
                )}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div className="space-y-1.5">
            <label htmlFor="login-phone" className="block text-xs font-medium text-gray-600">
              手机号
            </label>
            <input
              id="login-phone"
              type="tel"
              inputMode="numeric"
              autoComplete="tel"
              maxLength={11}
              value={phone}
              onChange={(e) => setPhone(e.target.value.replace(/\D/g, ""))}
              placeholder="13800138000"
              className="w-full h-11 px-3 text-base rounded-lg border border-gray-300 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 focus:outline-none"
            />
          </div>

          {mode === "code" ? (
            <div className="space-y-1.5">
              <label htmlFor="login-code" className="block text-xs font-medium text-gray-600">
                验证码
              </label>
              <div className="flex gap-2">
                <input
                  id="login-code"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={8}
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                  placeholder="6 位验证码"
                  className="flex-1 min-w-0 h-11 px-3 text-base rounded-lg border border-gray-300 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 focus:outline-none"
                />
                <Button
                  type="button"
                  variant="secondary"
                  className="h-11 shrink-0 tabular-nums"
                  onClick={() => void handleSendCode()}
                  disabled={sending || countdown > 0}
                >
                  {countdown > 0 ? `${countdown}s` : sending ? "发送中…" : "获取验证码"}
                </Button>
              </div>
              {debugCode && (
                <p className="text-xs text-amber-600 leading-relaxed">
                  开发模式验证码：<span className="font-mono font-semibold">{debugCode}</span>
                  ，已自动填入。这是开发模式验证码，生产环境不会返回。
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-1.5">
              <label htmlFor="login-password" className="block text-xs font-medium text-gray-600">
                密码
              </label>
              <input
                id="login-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="注册时设置的密码"
                className="w-full h-11 px-3 text-base rounded-lg border border-gray-300 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 focus:outline-none"
              />
              <p className="text-xs text-gray-400">验证码注册且未设置密码时，初始密码为手机号后 6 位</p>
            </div>
          )}

          {fieldError && <p className="text-xs text-red-500">{fieldError}</p>}

          <Button type="submit" size="lg" className="w-full" disabled={submitting}>
            {submitting ? "登录中…" : mode === "code" ? "注册 / 登录" : "登录"}
          </Button>

          <p className="text-xs text-gray-400 text-center leading-relaxed">
            {mode === "code"
              ? "新手机号将自动注册；已注册手机号会用初始密码登录"
              : "忘记密码？请联系管理员重置"}
          </p>
        </form>

        <p className="mt-4 text-center text-xs text-gray-400">
          <Link to="/" className="text-indigo-500 hover:text-indigo-600">
            先随便看看
          </Link>
          <span className="mx-2">·</span>
          <Link to="/settings" className="text-indigo-500 hover:text-indigo-600">
            设置
          </Link>
        </p>
      </div>
    </div>
  );
};

export default LoginPage;
