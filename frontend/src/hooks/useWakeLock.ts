import { useEffect, useRef } from "react";

/** 只保留实际用到的方法，避免与 TS 自带 DOM 类型冲突 */
type WakeLockSentinelLike = Pick<WakeLockSentinel, "release" | "released">;

/**
 * 处理期间保持手机屏幕常亮。
 *
 * 考试场景下用户盯着进度等答案，手机会自动锁屏，解锁会打断查看。
 * Screen Wake Lock 并非所有浏览器都支持（且要求安全上下文），
 * 因此这里全部做能力检测，不支持时静默降级，绝不因此报错。
 *
 * @param active 是否处于"正在处理"状态
 */
export function useWakeLock(active: boolean): void {
  const sentinelRef = useRef<WakeLockSentinelLike | null>(null);

  useEffect(() => {
    if (!active) return;

    // TS 的 DOM 类型里 wakeLock 是可选的，运行时同样可能不存在
    const wakeLock = navigator.wakeLock;
    if (!wakeLock) return;

    let cancelled = false;

    const request = async () => {
      try {
        const sentinel = await wakeLock.request("screen");
        if (cancelled) {
          // 组件已不再需要锁，立刻释放，避免"锁泄漏"
          void sentinel.release().catch(() => {});
          return;
        }
        sentinelRef.current = sentinel;
      } catch {
        // 用户拒绝 / 页面不可见 / 非安全上下文：忽略即可
      }
    };

    void request();

    // 切回前台时重新申请（浏览器会在页面隐藏时自动释放锁）
    const onVisible = () => {
      if (document.visibilityState === "visible") void request();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisible);
      const sentinel = sentinelRef.current;
      sentinelRef.current = null;
      if (sentinel && !sentinel.released) {
        void sentinel.release().catch(() => {});
      }
    };
  }, [active]);
}

/**
 * 答案就绪时轻微震动提示。
 *
 * 部分浏览器限制非手势触发的震动，失败时静默忽略。
 */
export function vibrateReady(pattern: number | number[] = 200): void {
  try {
    if (typeof navigator !== "undefined" && typeof navigator.vibrate === "function") {
      navigator.vibrate(pattern);
    }
  } catch {
    /* 不支持或权限不足时忽略 */
  }
}
