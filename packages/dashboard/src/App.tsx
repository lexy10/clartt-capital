import { Component, type ReactNode, type ErrorInfo, useEffect } from 'react';
import { RouterProvider } from 'react-router-dom';
import { router } from './routes';
import { useAuthStore } from './stores/authStore';
import { useThemeStore } from './stores/themeStore';

/** Loads the logged-in user's saved theme (from their DB record) when the user
 *  changes, and reverts to the shared default on logout. Renders nothing. */
function ThemeSync() {
  const userId = useAuthStore((s) => s.currentUser?.id ?? null);
  const dbTheme = useAuthStore((s) => s.currentUser?.theme ?? null);
  const loadForUser = useThemeStore((s) => s.loadForUser);
  useEffect(() => {
    loadForUser(userId, dbTheme);
    // Re-run only when the user identity changes, not on every theme tweak
    // (setMode/setAccent update currentUser.theme would otherwise re-load).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);
  return null;
}

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
      <ThemeSync />
      <RouterProvider router={router} />
    </ErrorBoundary>
  );
}

export default App;
