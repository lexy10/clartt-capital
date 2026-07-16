import { type FC, useEffect, useRef, useState } from 'react';
import { useThemeStore } from '../stores/themeStore';
import ThemePicker from './ThemePicker';

/**
 * Top-bar theme control: a dropdown (not a bare toggle) for picking colour mode
 * (light / dark / system) and accent. Choices persist to the user's account via
 * the theme store, so they follow the user across devices.
 */
const ThemeMenu: FC = () => {
  const mode = useThemeStore((s) => s.mode);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const resolved =
    mode === 'system'
      ? window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
      : mode;
  const label = mode === 'system' ? 'System' : mode === 'light' ? 'Light' : 'Dark';

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title="Theme"
        aria-haspopup="menu"
        aria-expanded={open}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          height: 30,
          padding: '0 8px',
          borderRadius: 'var(--radius-sm)',
          border: `1px solid ${open ? 'var(--accent)' : 'var(--glass-border)'}`,
          background: 'var(--bg-surface)',
          color: 'var(--text-secondary)',
          cursor: 'pointer',
          fontSize: 12,
          fontFamily: 'var(--font-sans)',
        }}
      >
        {resolved === 'dark' ? (
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
          </svg>
        ) : (
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
          </svg>
        )}
        <span>{label}</span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.7 }}>
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div
          role="menu"
          style={{
            position: 'absolute',
            top: 'calc(100% + 6px)',
            right: 0,
            zIndex: 100,
            minWidth: 240,
            padding: 14,
            borderRadius: 'var(--radius-md)',
            background: 'var(--panel-bg)',
            border: '1px solid var(--glass-border)',
            boxShadow: 'var(--shadow-lg)',
          }}
        >
          <ThemePicker />
        </div>
      )}
    </div>
  );
};

export default ThemeMenu;
