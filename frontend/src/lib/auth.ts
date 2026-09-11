import type { UserRole } from "../types";

/**
 * 登录令牌的本地存取与 JWT 载荷解析。
 *
 * 设计取舍：
 * - **不引入 jwt 库**。后端 `webapp/auth.py` 用标准库自实现 HS256，令牌就是
 *   标准 JWT；前端只需要读两个字段，为它拉一个依赖（含 polyfill）不值当。
 *   前端读到的载荷**只用于展示与路由判断**，不做任何安全决策——真正的鉴权
 *   始终由后端校验签名，所以这里不验签是安全的。
 * - 过期判断放在 `getAuthHeaders()` 里：发现令牌已过期就地清掉，避免把必然
 *   401 的请求打到后端，也让路由守卫拿到一致的"未登录"结论。
 */

/** localStorage 键名。改这里会让所有已登录用户掉线，不要随意改。 */
const TOKEN_KEY = "solver_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    // Safari 隐私模式等场景下 localStorage 会抛异常，退化为"未登录"而不是崩页
    return null;
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* 存不进去时本次会话仍可用内存里的 token，静默降级 */
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 忽略 */
  }
}

/** JWT 载荷中本项目用到的字段 */
export interface JwtPayload {
  sub?: string;
  role?: UserRole;
  tenant_id?: string;
  /** 签发时间（秒） */
  iat?: number;
  /** 过期时间（秒） */
  exp?: number;
}

/**
 * 解析 JWT 的 payload 段。
 *
 * 只做 base64url 解码，**不校验签名**。任何格式异常都返回 null，
 * 让调用方把令牌当作无效处理，而不是抛错中断渲染。
 */
export function parseJwtPayload(token: string | null | undefined): JwtPayload | null {
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;

  // base64url → base64：先把 -/_ 换回 +//，再补齐 '=' padding，
  // 否则 atob 对不足 4 的倍数长度会抛 InvalidCharacterError
  const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);

  try {
    // atob 返回的是 latin1 字符串，载荷是 JSON（ASCII 安全），无需再处理 UTF-8
    const json = atob(base64 + padding);
    const payload: unknown = JSON.parse(json);
    if (typeof payload !== "object" || payload === null) return null;
    return payload as JwtPayload;
  } catch {
    return null;
  }
}

/**
 * 令牌是否已过期。
 *
 * `skewSeconds` 是提前量：时钟略有偏差时不至于"刚签发就被判过期"。
 * 载荷里没有 `exp` 时按"不过期"处理，交给后端用 401 兜底。
 */
export function isTokenExpired(token: string | null | undefined, skewSeconds = 5): boolean {
  const payload = parseJwtPayload(token);
  if (!payload) return true;
  if (typeof payload.exp !== "number") return false;
  return Date.now() / 1000 >= payload.exp - skewSeconds;
}

/**
 * 当前是否处于"已登录"状态。
 *
 * 由于令牌过期即等于无效，这里顺带清掉过期令牌，让顶栏与路由守卫
 * 立刻看到未登录状态。
 */
export function isLoggedIn(): boolean {
  const token = getToken();
  if (!token) return false;
  if (isTokenExpired(token)) {
    clearToken();
    return false;
  }
  return true;
}

/** 从当前令牌读取角色；未登录或载荷异常时返回 null */
export function getTokenRole(): UserRole | null {
  if (!isLoggedIn()) return null;
  return parseJwtPayload(getToken())?.role ?? null;
}

/**
 * 请求头中的认证部分。
 *
 * 无令牌时返回空对象，而不是抛错或返回 `Bearer null`——
 * `AUTH_ENABLED=false` 的本地单用户模式下所有请求都不需要令牌。
 */
export function getAuthHeaders(): Record<string, string> {
  const token = getToken();
  if (!token || isTokenExpired(token)) {
    if (token) clearToken();
    return {};
  }
  return { Authorization: `Bearer ${token}` };
}
