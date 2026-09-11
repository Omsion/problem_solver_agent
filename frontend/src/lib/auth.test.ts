import { describe, expect, it } from "vitest";
import {
  clearToken,
  getAuthHeaders,
  getToken,
  getTokenRole,
  isLoggedIn,
  isTokenExpired,
  parseJwtPayload,
  setToken,
} from "./auth";

/**
 * 令牌存取 / JWT 载荷解析 / 登录态判断。
 *
 * 这里的重点是"前端读令牌只用于展示与路由判断"这条边界：
 * 任何畸形令牌都必须退化成 null / false，**不能抛异常**——否则路由守卫
 * 首帧就会炸掉整棵子树，手机端表现为整页空白。
 */

const TOKEN_KEY = "solver_token";

/** 按 base64url 规则编码（`+/` 换回 `-_`，并去掉 `=` padding） */
const base64url = (text: string): string =>
  btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

/**
 * 手工拼一个 JWT。签名段是随便写的：`parseJwtPayload` 只解码 payload，
 * 不验签（真正的鉴权始终在后端），所以这里只要能凑够三段即可。
 *
 * 注意 `btoa` 只接受 latin1，因此载荷必须是纯 ASCII——
 * 这也正是 auth.ts 里"载荷是 JSON（ASCII 安全）"那行注释的前提。
 */
const makeJwt = (payload: unknown, header: unknown = { alg: "HS256", typ: "JWT" }): string =>
  `${base64url(JSON.stringify(header))}.${base64url(JSON.stringify(payload))}.${base64url("test-signature")}`;

const secondsFromNow = (delta: number): number => Math.floor(Date.now() / 1000) + delta;

const validToken = (payload: Record<string, unknown> = {}): string =>
  makeJwt({ sub: "u1", role: "user", exp: secondsFromNow(3600), ...payload });

describe("令牌本地存取", () => {
  it("setToken / getToken / clearToken 往返正确，键名是 solver_token", () => {
    expect(getToken()).toBeNull();

    setToken("abc.def.ghi");
    expect(getToken()).toBe("abc.def.ghi");
    expect(localStorage.getItem(TOKEN_KEY)).toBe("abc.def.ghi");

    clearToken();
    expect(getToken()).toBeNull();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it("重复 setToken 覆盖旧值（重新登录后不会留下旧令牌）", () => {
    setToken("old");
    setToken("new");
    expect(getToken()).toBe("new");
  });

  it("clearToken 在没有令牌时也不抛异常", () => {
    expect(() => clearToken()).not.toThrow();
    clearToken();
    expect(getToken()).toBeNull();
  });
});

describe("parseJwtPayload", () => {
  it("解析合法 JWT 的载荷字段", () => {
    const token = makeJwt({ sub: "u-1", role: "admin", tenant_id: "t-9", iat: 1700000000, exp: 4102444800 });
    expect(parseJwtPayload(token)).toEqual({
      sub: "u-1",
      role: "admin",
      tenant_id: "t-9",
      iat: 1700000000,
      exp: 4102444800,
    });
  });

  it("payload 段含 base64url 专有字符 -/_ 时也能正确解码（base64url 的坑）", () => {
    // {"sub":"???>","role":"admin",...} 的 base64 里同时出现 '+' 与 '/'，
    // 换成 base64url 后就是 '-' 与 '_'。若不先把它们换回 '+'/'/'，
    // atob 会抛 InvalidCharacterError，把合法令牌误判成无效。
    const exp = secondsFromNow(3600);
    const token = makeJwt({ sub: "???>", role: "admin", exp });
    const segment = token.split(".")[1];

    // 先确认这个用例真的踩到了坑：段里必须同时有 - 和 _
    expect(segment).toMatch(/-/);
    expect(segment).toMatch(/_/);
    expect(parseJwtPayload(token)).toEqual({ sub: "???>", role: "admin", exp });
  });

  it("长度正好是 4 的倍数（无需补 padding）也能解码", () => {
    // JSON.stringify({a:1,bcd:2}) 恰好 15 字节 → base64 无 '=' padding
    const token = makeJwt({ a: 1, bcd: 2 });
    expect(btoa(JSON.stringify({ a: 1, bcd: 2 }))).not.toContain("=");
    expect(parseJwtPayload(token)).toEqual({ a: 1, bcd: 2 });
  });

  it("段数不对返回 null", () => {
    expect(parseJwtPayload("a.b")).toBeNull();
    expect(parseJwtPayload("only-one-part")).toBeNull();
    expect(parseJwtPayload("a.b.c.d")).toBeNull();
  });

  it("空载荷段（a..c）返回 null 而不是抛异常", () => {
    expect(() => parseJwtPayload("a..c")).not.toThrow();
    expect(parseJwtPayload("a..c")).toBeNull();
  });

  it("非法 base64 返回 null 而不是抛异常", () => {
    expect(() => parseJwtPayload("h.!!!!.s")).not.toThrow();
    expect(parseJwtPayload("h.!!!!.s")).toBeNull();
  });

  it("能解码但不是 JSON 的载荷返回 null", () => {
    // btoa("not json") === "bm90IGpzb24="
    expect(parseJwtPayload("h.bm90IGpzb24.s")).toBeNull();
  });

  it("JSON 合法但不是对象（数字 / null / 字符串）返回 null", () => {
    expect(parseJwtPayload(makeJwt(123))).toBeNull();
    expect(parseJwtPayload(makeJwt(null))).toBeNull();
    // JSON 字符串（"plain" → '"plain"'）也不是对象
    expect(parseJwtPayload(makeJwt("plain"))).toBeNull();
  });

  it("JSON 数组仍然是 object，会被原样当作载荷返回（当前实现如此）", () => {
    expect(parseJwtPayload(makeJwt([1, 2]))).toEqual([1, 2]);
  });

  it("空输入返回 null", () => {
    expect(parseJwtPayload(null)).toBeNull();
    expect(parseJwtPayload(undefined)).toBeNull();
    expect(parseJwtPayload("")).toBeNull();
  });
});

describe("isTokenExpired", () => {
  it("exp 已过 → true", () => {
    expect(isTokenExpired(makeJwt({ exp: secondsFromNow(-3600) }))).toBe(true);
  });

  it("exp 在未来 → false", () => {
    expect(isTokenExpired(makeJwt({ exp: secondsFromNow(3600) }))).toBe(false);
  });

  it("没有 exp 字段按不过期处理（交给后端 401 兜底）", () => {
    expect(isTokenExpired(makeJwt({ sub: "u1", role: "user" }))).toBe(false);
    expect(isTokenExpired(makeJwt({ exp: "not-a-number" }))).toBe(false);
  });

  it("非法令牌一律视为过期", () => {
    expect(isTokenExpired(null)).toBe(true);
    expect(isTokenExpired(undefined)).toBe(true);
    expect(isTokenExpired("")).toBe(true);
    expect(isTokenExpired("garbage")).toBe(true);
    expect(isTokenExpired("h.!!!!.s")).toBe(true);
  });

  it("默认 5 秒提前量：刚签发 3 秒的令牌也算过期", () => {
    const almostExpired = makeJwt({ exp: secondsFromNow(3) });
    expect(isTokenExpired(almostExpired)).toBe(true);
    // 提前量设为 0 时同一枚令牌仍然有效
    expect(isTokenExpired(almostExpired, 0)).toBe(false);
  });
});

describe("isLoggedIn", () => {
  it("没有令牌 → false", () => {
    expect(isLoggedIn()).toBe(false);
  });

  it("未过期令牌 → true", () => {
    setToken(validToken());
    expect(isLoggedIn()).toBe(true);
    // 未过期时不能被顺手清掉
    expect(getToken()).not.toBeNull();
  });

  it("过期令牌 → false 且就地清掉 localStorage（顶栏/守卫立刻看到未登录）", () => {
    setToken(makeJwt({ exp: secondsFromNow(-60) }));
    expect(isLoggedIn()).toBe(false);
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(getToken()).toBeNull();
  });

  it("畸形令牌 → false 且被清掉", () => {
    setToken("not-a-jwt");
    expect(isLoggedIn()).toBe(false);
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });
});

describe("getAuthHeaders", () => {
  it("有有效令牌时返回 Bearer 头", () => {
    const token = validToken();
    setToken(token);
    expect(getAuthHeaders()).toEqual({ Authorization: `Bearer ${token}` });
    // 有效令牌不应被清掉
    expect(getToken()).toBe(token);
  });

  it("无令牌时返回空对象（本地单用户模式不需要令牌）", () => {
    expect(getAuthHeaders()).toEqual({});
  });

  it("令牌已过期时返回空对象并清掉它（不把必然 401 的请求打出去）", () => {
    setToken(makeJwt({ exp: secondsFromNow(-60) }));
    expect(getAuthHeaders()).toEqual({});
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it("畸形令牌同样被清掉", () => {
    setToken("h.!!!!.s");
    expect(getAuthHeaders()).toEqual({});
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });
});

describe("getTokenRole", () => {
  it("无令牌返回 null", () => {
    expect(getTokenRole()).toBeNull();
  });

  it("从载荷读出 role", () => {
    setToken(validToken({ role: "admin" }));
    expect(getTokenRole()).toBe("admin");

    setToken(validToken({ role: "user" }));
    expect(getTokenRole()).toBe("user");
  });

  it("载荷里没有 role 时返回 null（而不是 undefined）", () => {
    setToken(makeJwt({ sub: "u1", exp: secondsFromNow(3600) }));
    expect(getTokenRole()).toBeNull();
  });

  it("过期令牌返回 null 并清掉令牌", () => {
    setToken(makeJwt({ role: "admin", exp: secondsFromNow(-60) }));
    expect(getTokenRole()).toBeNull();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });
});
