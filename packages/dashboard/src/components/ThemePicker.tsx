import { type FC } from 'react';
import { useThemeStore, ACCENTS, type ThemeMode } from '../stores/themeStore';

/**
 * Full theme control: colour mode (light / dark / system) + accent preset.
 * Preferences are saved per-user, so this only affects the signed-in user.
 */
const MODES: { id: ThemeMode; label: string }[] = [
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
  { id: 'system', label: 'System' },
];

const ThemePicker: FC = () => {
  const mode = useThemeStore((s) => s.mode);
  const accent = useThemeStore((s) => s.accent);
  const setMode = useThemeStore((s) => s.setMode);
  const setAccent = useThemeStore((s) => s.setAccent);

  return (
    <div>
      <label style={label}>Colour mode</label>
      <div style={{ display: 'flex', gap: 6 }}>
        {MODES.map((m) => (
          <button
            key={m.id}
            type="button"
            onClick={() => setMode(m.id)}
            style={{
              flex: 1,
              padding: '7px 0',
              fontSize: 12,
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              cursor: 'pointer',
              border: `1px solid ${mode === m.id ? 'var(--accent)' : 'var(--glass-border)'}`,
              background: mode === m.id ? 'var(--accent-dim)' : 'var(--bg-surface)',
              color: mode === m.id ? 'var(--accent)' : 'var(--text-secondary)',
              transition: 'all var(--transition-fast)',
            }}
          >
            {m.label}
          </button>
        ))}
      </div>

      <label style={{ ...label, marginTop: 14 }}>Accent</label>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        {ACCENTS.map((a) => {
          const active = accent === a.id;
          return (
            <button
              key={a.id}
              type="button"
              onClick={() => setAccent(a.id)}
              title={a.label}
              aria-label={a.label}
              style={{
                width: 26,
                height: 26,
                borderRadius: '50%',
                cursor: 'pointer',
                background: a.swatch,
                border: `2px solid ${active ? 'var(--text-primary)' : 'transparent'}`,
                boxShadow: active ? '0 0 0 2px var(--bg-secondary)' : 'none',
                transition: 'transform var(--transition-fast)',
                transform: active ? 'scale(1.08)' : 'scale(1)',
              }}
            />
          );
        })}
      </div>
    </div>
  );
};

const label: React.CSSProperties = {
  display: 'block',
  fontSize: 10,
  fontWeight: 600,
  color: 'var(--text-muted)',
  textTransform: 'uppercase',
  letterSpacing: 0.6,
  margin: '0 0 6px',
};

export default ThemePicker;
