import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./index.css";
import App from "./App";
import { ConfirmProvider } from "./components/ui/confirm";
import { Toasts } from "./components/ui/toast";

/**
 * 全局 Provider。
 *
 * - QueryClientProvider：任务列表/详情/统计的服务端数据缓存与失效
 * - ConfirmProvider：Promise 风格的确认框（替代 window.confirm）
 * - Toasts：Sonner 轻提示（替代 alert 与内联横幅）
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 任务状态变化频繁，窗口聚焦时重新拉取以保持一致
      refetchOnWindowFocus: true,
      retry: 1,
      staleTime: 5_000,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfirmProvider>
        <App />
        <Toasts />
      </ConfirmProvider>
    </QueryClientProvider>
  </StrictMode>,
);
