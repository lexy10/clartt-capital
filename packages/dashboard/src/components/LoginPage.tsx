import { useState, type FC, type FormEvent } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuthStore } from '../stores/authStore';

const LoginPage: FC = () => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const { login, error, loading, isAuthenticated } = useAuthStore();

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    await login(email, password);
  };

  if (isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      height: '100vh',
      background: 'var(--bg-primary)',
      fontFamily: 'var(--font-sans)',
    }}>
      <form onSubmit={handleSubmit} style={{
        background: 'var(--glass-bg)',
        backdropFilter: 'blur(var(--glass-blur))',
        WebkitBackdropFilter: 'blur(var(--glass-blur))',
        border: '1px solid var(--glass-border)',
        borderRadius: 'var(--radius-xl)',
        padding: 40,
        width: 380,
        display: 'flex',
        flexDirection: 'column',
        gap: 20,
        boxShadow: 'var(--shadow-lg)',
      }}>
        {/* Logo */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, marginBottom: 8 }}>
          <div style={{
            width: 48, height: 48, borderRadius: 14,
            background: 'linear-gradient(135deg, var(--accent) 0%, #6366f1 100%)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            boxShadow: '0 0 24px rgba(129, 140, 248, 0.25)',
          }}>
            <span style={{ fontSize: 16, fontWeight: 700, color: '#fff', fontFamily: 'var(--font-mono)' }}>CC</span>
          </div>
          <h2 style={{ color: 'var(--text-primary)', margin: 0, fontSize: 18, fontWeight: 600 }}>
            Clartt Capital
          </h2>
          <p style={{ color: 'var(--text-muted)', fontSize: 12, margin: 0 }}>Sign in to your account</p>
        </div>

        {error && (
          <div style={{
            color: 'var(--danger)', background: 'var(--danger-bg)',
            border: '1px solid var(--danger-border)',
            padding: '8px 12px', borderRadius: 'var(--radius-md)', fontSize: 12,
          }}>{error}</div>
        )}

        <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span style={{ color: 'var(--text-secondary)', fontSize: 12, fontWeight: 500 }}>Email</span>
          <input
            type="email" value={email} onChange={(e) => setEmail(e.target.value)}
            required autoFocus placeholder="you@example.com"
            style={{
              background: 'var(--bg-surface)', border: '1px solid var(--border-primary)',
              borderRadius: 'var(--radius-md)', padding: '10px 14px',
              color: 'var(--text-primary)', fontSize: 13, outline: 'none',
              transition: 'border-color var(--transition-fast)',
            }}
            onFocus={(e) => { e.currentTarget.style.borderColor = 'var(--accent)'; }}
            onBlur={(e) => { e.currentTarget.style.borderColor = 'var(--border-primary)'; }}
          />
        </label>

        <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span style={{ color: 'var(--text-secondary)', fontSize: 12, fontWeight: 500 }}>Password</span>
          <input
            type="password" value={password} onChange={(e) => setPassword(e.target.value)}
            required placeholder="••••••••"
            style={{
              background: 'var(--bg-surface)', border: '1px solid var(--border-primary)',
              borderRadius: 'var(--radius-md)', padding: '10px 14px',
              color: 'var(--text-primary)', fontSize: 13, outline: 'none',
              transition: 'border-color var(--transition-fast)',
            }}
            onFocus={(e) => { e.currentTarget.style.borderColor = 'var(--accent)'; }}
            onBlur={(e) => { e.currentTarget.style.borderColor = 'var(--border-primary)'; }}
          />
        </label>

        <button
          type="submit" disabled={loading}
          style={{
            background: 'linear-gradient(135deg, var(--accent) 0%, #6366f1 100%)',
            color: '#fff', border: 'none', borderRadius: 'var(--radius-md)',
            padding: '11px', fontSize: 13, fontWeight: 600,
            cursor: loading ? 'wait' : 'pointer', opacity: loading ? 0.7 : 1,
            transition: 'all var(--transition-fast)',
            boxShadow: '0 2px 12px rgba(129, 140, 248, 0.3)',
            marginTop: 4,
          }}
        >
          {loading ? 'Signing in…' : 'Sign In'}
        </button>
      </form>
    </div>
  );
};

export default LoginPage;
