import { Component, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error?: Error;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[Pan] React Error Boundary caught:', error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-bg-primary">
          <div className="text-center">
            <h1 className="text-xl font-semibold text-danger mb-2">
              页面出错了
            </h1>
            <p className="text-text-secondary mb-4 text-sm">
              {this.state.error?.message ?? 'Unknown error'}
            </p>
            <div className="flex gap-3 justify-center">
              <button
                onClick={() => window.location.reload()}
                className="rounded bg-accent px-4 py-2 text-sm text-white hover:bg-accent-hover"
              >
                刷新页面
              </button>
              <a
                href="/"
                className="rounded border border-border-default px-4 py-2 text-sm text-text-primary hover:bg-bg-tertiary"
              >
                返回旧版
              </a>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
