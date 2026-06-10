import { Component, type ReactNode, type ErrorInfo } from 'react';
import { RouterProvider } from 'react-router-dom';
import { router } from './routes';

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('React error:', error, info); }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 32, color: 'var(--danger)', background: 'var(--bg-primary)', height: '100vh', fontFamily: 'var(--font-mono)' }}>
          <h2>Something went wrong</h2>
          <pre style={{ whiteSpace: 'pre-wrap', color: 'var(--text-primary)', marginTop: 16 }}>{this.state.error.message}</pre>
        </div>
      );
    }
    return this.props.children;
  }
}
function App() {
  return (
    <ErrorBoundary>
      <RouterProvider router={router} />
    </ErrorBoundary>
  );
}

export default App;
