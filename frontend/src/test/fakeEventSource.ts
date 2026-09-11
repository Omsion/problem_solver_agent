/**
 * EventSource 的最小测试替身。
 *
 * 真实实现由浏览器提供；这里只需要支持被测代码用到的部分：
 * 具名事件监听、open/error 回调、close，以及记录 URL（用于断言重连行为）。
 */
export class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly url: string;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;

  private listeners = new Map<string, Set<(event: MessageEvent) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }

  removeEventListener(type: string, listener: (event: MessageEvent) => void): void {
    this.listeners.get(type)?.delete(listener);
  }

  close(): void {
    this.closed = true;
  }

  // ---- 测试辅助 ----

  /** 模拟服务端推送一条具名事件 */
  emit(type: string, data: unknown): void {
    const payload = typeof data === "string" ? data : JSON.stringify(data);
    const event = { data: payload } as MessageEvent;
    this.listeners.get(type)?.forEach((listener) => listener(event));
  }

  /** 模拟连接建立 */
  open(): void {
    this.onopen?.(new Event("open"));
  }

  /** 模拟连接中断 */
  fail(): void {
    this.onerror?.(new Event("error"));
  }

  static reset(): void {
    // 只清空记录，不动全局 stub —— 清掉 stub 会让后续 connectSSE 拿不到替身
    FakeEventSource.instances = [];
  }

  static get last(): FakeEventSource {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1];
  }
}
