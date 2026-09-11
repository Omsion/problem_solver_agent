import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { RequireAdmin, RequireAuth } from "./AuthRoutes";
import { safeRedirectPath } from "../../hooks/useAuth";

/**
 * 路由守卫测试。
 *
 * 这里必须 mock `useAuth` 而不是渲染真 QueryClient：
 * 守卫的判断完全由 `{ user, authEnabled, isLoading }` 三个字段决定，
 * 用真查询只会把测试变成"等网络/等缓存"。同一个模块里的纯函数
 * `safeRedirectPath` 则保留真实实现（见文件末尾）。
 *
 * 被测行为里最关键的是两条 fail-open 边界：
 * - `auth_enabled === false`（本地单用户模式）→ 无用户也必须放行；
 * - `auth_enabled === null`（探测中 / 请求失败）→ 放行而不是跳登录页。
 * 任何一条退化都会让本地模式首帧弹登录页或整页空白。
 */

/**
 * `vi.hoisted` 让这份状态在 vi.mock 工厂之前就存在。
 *
 * 普通模块级 const 会被 ESM import 提升甩在后面，工厂首次执行时读到的是
 * TDZ 里的绑定，会直接 ReferenceError。
 */
const authStub = vi.hoisted(() => ({
  user: null as { id: string; phone: string; role: "user" | "admin" } | null,
  authEnabled: null as boolean | null,
  isLoading: false,
}));

/**
 * 只替换 `useAuth`，其余导出（`safeRedirectPath`）保留真实实现——
 * 白名单校验是纯函数，没有理由连它一起桩掉。
 */
vi.mock("../../hooks/useAuth", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../hooks/useAuth")>();
  return { ...actual, useAuth: () => authStub };
});

/** 占位"登录页"：把守卫透传过来的 `state.from` 显示出来供断言 */
const LoginProbe = () => {
  const location = useLocation();
  const state = location.state as { from?: string } | null;
  return <div data-testid="login-state">{state?.from ?? "no-from"}</div>;
};

const renderProtected = (path = "/settings?tab=usage") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<LoginProbe />} />
        <Route
          path="*"
          element={
            <RequireAuth>
              <div>受保护内容</div>
            </RequireAuth>
          }
        />
      </Routes>
    </MemoryRouter>,
  );

const renderAdmin = () =>
  render(
    <MemoryRouter initialEntries={["/admin"]}>
      <Routes>
        <Route
          path="/admin"
          element={
            <RequireAdmin>
              <div>管理看板</div>
            </RequireAdmin>
          }
        />
      </Routes>
    </MemoryRouter>,
  );

beforeEach(() => {
  authStub.user = null;
  authStub.authEnabled = null;
  authStub.isLoading = false;
});

describe("RequireAuth", () => {
  it("auth_enabled=true 且未登录 → 跳到 /login，并把原路径+查询串放进 state.from", () => {
    authStub.authEnabled = true;
    authStub.user = null;

    renderProtected("/settings?tab=usage");

    expect(screen.getByTestId("login-state")).toHaveTextContent("/settings?tab=usage");
    expect(screen.queryByText("受保护内容")).not.toBeInTheDocument();
  });

  it("auth_enabled=true 且已登录 → 渲染子内容，不跳登录页", () => {
    authStub.authEnabled = true;
    authStub.user = { id: "u1", phone: "138****8000", role: "user" };

    renderProtected();

    expect(screen.getByText("受保护内容")).toBeInTheDocument();
    expect(screen.queryByTestId("login-state")).not.toBeInTheDocument();
  });

  it("auth_enabled=false → 即使无用户也放行（本地单用户模式绝不强制登录）", () => {
    authStub.authEnabled = false;
    authStub.user = null;

    renderProtected();

    expect(screen.getByText("受保护内容")).toBeInTheDocument();
    expect(screen.queryByTestId("login-state")).not.toBeInTheDocument();
  });

  it("auth_enabled=null 且仍在探测 → 渲染空占位，既不跳登录也不渲染页面", () => {
    authStub.authEnabled = null;
    authStub.isLoading = true;

    renderProtected();

    expect(screen.queryByTestId("login-state")).not.toBeInTheDocument();
    expect(screen.queryByText("受保护内容")).not.toBeInTheDocument();
  });

  it("auth_enabled=null 且探测已结束（/me 失败）→ fail-open 放行，避免白屏", () => {
    authStub.authEnabled = null;
    authStub.isLoading = false;
    authStub.user = null;

    renderProtected();

    expect(screen.getByText("受保护内容")).toBeInTheDocument();
    expect(screen.queryByTestId("login-state")).not.toBeInTheDocument();
  });

  it("传了 fallback 时，探测未完成用 fallback 兜底", () => {
    authStub.authEnabled = null;
    authStub.isLoading = false;

    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/login" element={<LoginProbe />} />
          <Route
            path="*"
            element={
              <RequireAuth fallback={<div>探测失败的兜底文案</div>}>
                <div>受保护内容</div>
              </RequireAuth>
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText("探测失败的兜底文案")).toBeInTheDocument();
    expect(screen.queryByText("受保护内容")).not.toBeInTheDocument();
    expect(screen.queryByTestId("login-state")).not.toBeInTheDocument();
  });
});

describe("RequireAdmin", () => {
  it("非 admin → 渲染'需要管理员权限'提示而不是崩溃或静默重定向", () => {
    authStub.authEnabled = true;
    authStub.user = { id: "u1", phone: "138****8000", role: "user" };

    renderAdmin();

    expect(screen.getByText("需要管理员权限")).toBeInTheDocument();
    expect(screen.getByText("当前账号不是管理员，无法查看用户与用量看板")).toBeInTheDocument();
    expect(screen.queryByText("管理看板")).not.toBeInTheDocument();
  });

  it("未登录（user 为 null）→ 同样渲染拒绝提示", () => {
    authStub.authEnabled = true;
    authStub.user = null;

    renderAdmin();

    expect(screen.getByText("需要管理员权限")).toBeInTheDocument();
    expect(screen.queryByText("管理看板")).not.toBeInTheDocument();
  });

  it("admin → 渲染子内容", () => {
    authStub.authEnabled = true;
    authStub.user = { id: "u1", phone: "138****8000", role: "admin" };

    renderAdmin();

    expect(screen.getByText("管理看板")).toBeInTheDocument();
    expect(screen.queryByText("需要管理员权限")).not.toBeInTheDocument();
  });

  it("本地模式（auth_enabled=false）下的 admin 也能进", () => {
    authStub.authEnabled = false;
    authStub.user = { id: "local", phone: "local", role: "admin" };

    renderAdmin();

    expect(screen.getByText("管理看板")).toBeInTheDocument();
  });

  it("探测中 → 渲染空占位而不是拒绝提示（避免闪一下'需要管理员权限'）", () => {
    authStub.authEnabled = null;
    authStub.isLoading = true;

    renderAdmin();

    expect(screen.queryByText("需要管理员权限")).not.toBeInTheDocument();
    expect(screen.queryByText("管理看板")).not.toBeInTheDocument();
  });
});

/**
 * 登录后回跳路径的白名单。
 *
 * `location.state` 可以被任何脚本伪造，直接 `navigate(from)` 就是开放重定向，
 * 因此只有"以单个 `/` 开头的站内路径"才允许通过。
 */
describe("safeRedirectPath", () => {
  it("站内路径原样返回（含查询串）", () => {
    expect(safeRedirectPath("/settings?tab=usage")).toBe("/settings?tab=usage");
    expect(safeRedirectPath("/admin")).toBe("/admin");
    expect(safeRedirectPath("/")).toBe("/");
  });

  it("拒绝协议相对地址 //evil.com（开放重定向）", () => {
    expect(safeRedirectPath("//evil.com")).toBe("/");
    expect(safeRedirectPath("//evil.com/login?x=1")).toBe("/");
  });

  it("拒绝绝对 URL 与非斜杠开头的字符串", () => {
    expect(safeRedirectPath("https://evil.com")).toBe("/");
    expect(safeRedirectPath("http://evil.com/x")).toBe("/");
    expect(safeRedirectPath("javascript:alert(1)")).toBe("/");
    expect(safeRedirectPath("evil.com/x")).toBe("/");
    expect(safeRedirectPath("")).toBe("/");
  });

  it("拒绝非字符串类型（篡改过的 state）", () => {
    expect(safeRedirectPath(undefined)).toBe("/");
    expect(safeRedirectPath(null)).toBe("/");
    expect(safeRedirectPath(123)).toBe("/");
    expect(safeRedirectPath({ pathname: "/admin" })).toBe("/");
  });

  it("支持自定义 fallback", () => {
    expect(safeRedirectPath("//evil.com", "/usage")).toBe("/usage");
    expect(safeRedirectPath(undefined, "/usage")).toBe("/usage");
  });
});
