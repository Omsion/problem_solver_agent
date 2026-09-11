import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, globalSseUrl, login, sseUrl } from "./api";
import { clearToken, setToken } from "./auth";

/**
 * API 错误体解析与 SSE 地址拼装的测试。
 *
 * `parseError` 是模块私有函数，这里通过公开端点 `login()` 间接覆盖：
 * 桩掉全局 `fetch` 返回一个真实的 `Response`（一定不发网络请求），
 * 再断言抛出的 `ApiError` 的 message / code / status。
 *
 * 后端并存三种错误体（`{"error": 字符串}`、`{"error": {code,message}}`、
 * `{"detail": 字符串 | {code,message}}`），任何一条没解析到都只会让界面
 * 显示兜底文案，用户看不到"到底哪里错了"。
 */

const LOGIN_ARGS = { phone: "13800138000", password: "secret" };

/** 桩掉 fetch，返回一个指定状态码与响应体的真实 Response */
const stubFetch = (body: string | null, status = 401) => {
  const fetchMock = vi.fn(async () => new Response(body, { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

/** 断言 promise 被拒，并把 ApiError 交回调用方 */
const expectApiError = async (promise: Promise<unknown>): Promise<ApiError> => {
  const rejected = await promise.then(
    () => null,
    (error: unknown) => error,
  );
  expect(rejected, "应当抛出 ApiError").toBeInstanceOf(ApiError);
  return rejected as ApiError;
};

const base64url = (text: string): string =>
  btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

/** 一枚已过期的令牌（exp 在 60 秒前） */
const expiredToken = (): string =>
  `${base64url(JSON.stringify({ alg: "HS256", typ: "JWT" }))}.${base64url(
    JSON.stringify({ sub: "u1", role: "user", exp: Math.floor(Date.now() / 1000) - 60 }),
  )}.${base64url("sig")}`;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("parseError（经 login() 间接覆盖）", () => {
  it("detail 是对象时取出 message 与 code", async () => {
    stubFetch(JSON.stringify({ detail: { code: "unauthorized", message: "登录令牌无效，请重新登录" } }));

    const error = await expectApiError(login(LOGIN_ARGS));

    expect(error.message).toBe("登录令牌无效，请重新登录");
    expect(error.code).toBe("unauthorized");
    expect(error.status).toBe(401);
    expect(error.name).toBe("ApiError");
    expect(error).toBeInstanceOf(Error);
  });

  it("detail 是纯字符串时用字符串作为 message，且没有 code", async () => {
    stubFetch(JSON.stringify({ detail: "手机号或密码错误" }));

    const error = await expectApiError(login(LOGIN_ARGS));

    expect(error.message).toBe("手机号或密码错误");
    expect(error.code).toBeUndefined();
    expect(error.status).toBe(401);
  });

  it("detail 对象没有 code 时只取 message", async () => {
    stubFetch(JSON.stringify({ detail: { message: "验证码已过期" } }));

    const error = await expectApiError(login(LOGIN_ARGS));

    expect(error.message).toBe("验证码已过期");
    expect(error.code).toBeUndefined();
  });

  it("兼容旧格式 {error: 字符串}", async () => {
    stubFetch(JSON.stringify({ error: "旧格式错误文案" }));

    const error = await expectApiError(login(LOGIN_ARGS));

    expect(error.message).toBe("旧格式错误文案");
    expect(error.code).toBeUndefined();
  });

  it("兼容旧格式 {error: {code, message}}", async () => {
    stubFetch(JSON.stringify({ error: { code: "quota_exceeded", message: "额度不足" } }));

    const error = await expectApiError(login(LOGIN_ARGS));

    expect(error.message).toBe("额度不足");
    expect(error.code).toBe("quota_exceeded");
  });

  it("响应体不是 JSON 时走 fallback 文案且不抛解析异常", async () => {
    stubFetch("<html><body>502 Bad Gateway</body></html>", 401);

    const error = await expectApiError(login(LOGIN_ARGS));

    // 不是 SyntaxError，而是带状态码的兜底文案
    expect(error.message).toBe("登录失败（401）");
    expect(error.code).toBeUndefined();
    expect(error.status).toBe(401);
  });

  it("空响应体（500）同样走 fallback", async () => {
    stubFetch("", 500);

    const error = await expectApiError(login(LOGIN_ARGS));

    expect(error.message).toBe("登录失败（500）");
    expect(error.status).toBe(500);
  });

  it("detail 是无 message 的对象时走 fallback", async () => {
    stubFetch(JSON.stringify({ detail: {} }), 403);

    const error = await expectApiError(login(LOGIN_ARGS));

    expect(error.message).toBe("登录失败（403）");
    expect(error.code).toBeUndefined();
  });

  it("只发一次请求，且打到 v1 登录端点（全程使用替身，无真实网络）", async () => {
    const fetchMock = stubFetch(JSON.stringify({ detail: "登录失败" }));

    await expectApiError(login(LOGIN_ARGS));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/login",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("成功响应（200）正常返回，不构造 ApiError", async () => {
    stubFetch(
      JSON.stringify({
        access_token: "abc",
        token_type: "bearer",
        expires_in_minutes: 60,
        user: { id: "u1", phone: "138****8000", role: "user" },
      }),
      200,
    );

    const res = await login(LOGIN_ARGS);
    expect(res.access_token).toBe("abc");
  });
});

describe("SSE 地址携带登录令牌", () => {
  it("有令牌时 sseUrl 带上 token=", () => {
    setToken("abc");

    expect(sseUrl("t1")).toBe("/api/tasks/t1/stream?token=abc");
    expect(sseUrl("t1")).toContain("token=abc");
  });

  it("有令牌时 globalSseUrl 带上 token=", () => {
    setToken("abc");

    expect(globalSseUrl()).toBe("/api/events/stream?token=abc");
    expect(globalSseUrl()).toContain("token=abc");
  });

  it("clearToken 之后地址里不再有 token 参数", () => {
    setToken("abc");
    expect(sseUrl("t1")).toContain("token=");

    clearToken();

    expect(sseUrl("t1")).toBe("/api/tasks/t1/stream");
    expect(sseUrl("t1")).not.toContain("token=");
    expect(globalSseUrl()).toBe("/api/events/stream");
    expect(globalSseUrl()).not.toContain("token=");
  });

  it("sseUrl(id, true) 同时含 thinking=1 与 token=", () => {
    setToken("abc");
    const url = sseUrl("t1", true);

    expect(url).toContain("thinking=1");
    expect(url).toContain("token=abc");
    expect(url).toBe("/api/tasks/t1/stream?thinking=1&token=abc");
  });

  it("thinking 为 false 时不带 thinking 参数", () => {
    setToken("abc");
    expect(sseUrl("t1", false)).toBe("/api/tasks/t1/stream?token=abc");
  });

  it("任务 id 与令牌都做 URL 编码", () => {
    setToken("a b+c/d");

    // 令牌里的空格/加号/斜杠必须编码，否则 query 会被截断或语义变化
    expect(sseUrl("t1")).toBe("/api/tasks/t1/stream?token=a%20b%2Bc%2Fd");
    expect(sseUrl("a/b c", true)).toBe("/api/tasks/a%2Fb%20c/stream?thinking=1&token=a%20b%2Bc%2Fd");
  });

  it("过期令牌仍会被写进 SSE 地址（当前实现如此，与 getAuthHeaders 的清理行为不一致）", () => {
    setToken(expiredToken());

    // 记录现状：withStreamAuth 直接读 getToken()，不做过期判断，
    // 因此这里仍然带上 token=；而 getAuthHeaders() 会返回 {} 并清掉令牌。
    // 详见交付报告中的"源码观察"。
    expect(sseUrl("t1")).toContain("token=");
    expect(globalSseUrl()).toContain("token=");
  });
});
