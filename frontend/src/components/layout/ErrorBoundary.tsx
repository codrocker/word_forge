import { Component, type ReactNode } from 'react';
import { Button } from '@douyinfe/semi-ui';

type Props = { children: ReactNode };
type State = { error: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string }) {
    // eslint-disable-next-line no-console
    console.error('Unhandled error caught by ErrorBoundary:', error, info);
  }

  render() {
    if (this.state.error) {
      const requestId = (this.state.error as Error & { requestId?: string }).requestId;
      return (
        <div className="min-h-screen flex items-center justify-center p-6">
          <div className="max-w-md text-center">
            <h1 className="text-2xl font-semibold mb-4">哎呀,出错了</h1>
            <p className="text-gray-700 mb-2">页面遇到未处理的错误。</p>
            <p className="text-sm text-gray-500 mb-4">{this.state.error.message}</p>
            {requestId && (
              <p className="text-xs text-gray-400 mb-4">request_id: {requestId}</p>
            )}
            <Button theme="solid" htmlType="button" onClick={() => window.location.reload()}>
              刷新重试
            </Button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
