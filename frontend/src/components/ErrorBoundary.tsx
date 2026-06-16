import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** 出错时展示的标题，便于定位是哪一块面板坏了 */
  title?: string;
  /** 出错时的回调（可用于上报） */
  onError?: (error: Error, info: ErrorInfo) => void;
}

interface State {
  error: Error | null;
}

/**
 * 渲染期错误边界。
 *
 * 缺陷 D 的教训：手机端"一片空白"很可能就是某个子组件在渲染期抛错
 * （例如把 NaN 传给 toLocaleString），React 会卸载整棵子树且不给任何提示。
 * 有了边界之后，至少能显示原因并提供重试。
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[ErrorBoundary]", this.props.title ?? "面板", error, info.componentStack);
    this.props.onError?.(error, info);
  }

  private handleReset = () => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="flex flex-col items-center justify-center gap-3 h-full p-6 text-center">
        <div className="bg-red-50 border border-red-200 rounded-xl p-5 max-w-md w-full">
          <p className="text-red-600 font-medium text-sm">{this.props.title ?? "界面出错"}</p>
          <p className="text-red-500 text-xs mt-1 break-words">{error.message || "未知错误"}</p>
        </div>
        <button
          onClick={this.handleReset}
          className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg cursor-pointer"
        >
          重试
        </button>
        <button
          onClick={() => window.location.reload()}
          className="text-xs text-indigo-500 hover:text-indigo-600 cursor-pointer"
        >
          刷新页面
        </button>
      </div>
    );
  }
}
