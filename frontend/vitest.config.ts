import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

/**
 * 前端单元测试配置。
 *
 * 与 vite.config.ts 分开，避免把构建用的 base/outDir 等设置带进测试。
 * 测试环境用 jsdom（需要 DOM 与 EventSource）。
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    css: false,
    restoreMocks: true,
  },
});
