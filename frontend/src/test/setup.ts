import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

/**
 * 测试环境初始化。
 *
 * jsdom 不实现一些浏览器 API，这里补上被测代码会用到的部分：
 * - matchMedia：useMediaQuery 依赖
 * - ResizeObserver：Radix 部分组件在挂载时会用到
 *
 * 注意：**不要**在这里定义 navigator.clipboard —— @testing-library/user-event
 * 的 setup() 会自己去挂一个剪贴板替身，若此处先定义了不可配置属性，
 * setup() 会抛 "Cannot redefine property: clipboard"。
 */

if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

if (!window.ResizeObserver) {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

afterEach(() => {
  cleanup();
  localStorage.clear();
});
