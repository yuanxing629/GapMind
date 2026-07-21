import { Component, type ErrorInfo, type ReactNode } from "react";
import { Result, Button } from "antd";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error("ErrorBoundary caught:", error, errorInfo);
    this.setState({ errorInfo });
  }

  handleReload = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
    window.location.reload();
  };

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <Result
          status="error"
          title="Render Error"
          subTitle={this.state.error?.message ?? "Unknown error"}
          extra={
            <Button type="primary" onClick={this.handleReload}>
              Reload
            </Button>
          }
        >
          <pre
            style={{
              textAlign: "left",
              background: "#f5f5f5",
              padding: 12,
              borderRadius: 4,
              fontSize: 12,
              overflow: "auto",
              maxHeight: 300,
            }}
          >
            {this.state.error?.stack}
            {this.state.errorInfo?.componentStack}
          </pre>
        </Result>
      );
    }
    return this.props.children;
  }
}
