import { type FC, useState, useEffect, useCallback, useRef } from 'react';
import { apiClient } from '../services/ApiClient';
import { useAutopilotStore } from '../stores/autopilotStore';

const AutopilotToggle: FC = () => {
  const enabled = useAutopilotStore((s) => s.enabled);
  const setEnabled = useAutopilotStore((s) => s.setEnabled);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const errorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Fetch master autopilot state on mount
  useEffect(() => {
    let cancelled = false;
    apiClient.autopilot.getMaster().then((state) => {
      if (!cancelled) setEnabled(state.enabled);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [setEnabled]);

  const showError = useCallback((msg: string) => {
    setErrorMsg(msg);
    if (errorTimerRef.current) clearTimeout(errorTimerRef.current);
    errorTimerRef.current = setTimeout(() => setErrorMsg(null), 4000);
  }, []);

  useEffect(() => {
    return () => { if (errorTimerRef.current) clearTimeout(errorTimerRef.current); };
  }, []);

  const handleToggle = useCallback(async () => {
    if (loading) return;
    const prev = enabled;
    const next = !enabled;
    setEnabled(next);
    setLoading(true);
    try {
      await apiClient.autopilot.setMaster(next);
    } catch (err: unknown) {
      setEnabled(prev);
      showError(err instanceof Error ? err.message : 'Failed to update autopilot');
    } finally {
      setLoading(false);
    }
  }, [enabled, loading, setEnabled, showError]);

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{
          fontSize: 11,
          fontWeight: 600,
          color: enabled ? 'var(--accent)' : 'var(--text-muted)',
          letterSpacing: '0.5px',
        }}>
          Autopilot
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-label={`Autopilot ${enabled ? 'enabled' : 'disabled'}`}
          aria-busy={loading}
          disabled={loading}
          onClick={handleToggle}
          style={{
            position: 'relative',
            width: 38,
            height: 20,
            borderRadius: 10,
            background: enabled ? 'var(--accent)' : 'var(--bg-surface)',
            border: `1px solid ${enabled ? 'var(--accent)' : 'var(--border-primary)'}`,
            cursor: loading ? 'not-allowed' : 'pointer',
            transition: 'all 250ms cubic-bezier(0.4, 0, 0.2, 1)',
            boxShadow: enabled ? '0 0 12px rgba(129, 140, 248, 0.35), 0 0 4px rgba(129, 140, 248, 0.2)' : 'none',
            opacity: loading ? 0.4 : 1,
            flexShrink: 0,
          }}
        >
          <span style={{
            position: 'absolute',
            top: 2,
            left: enabled ? 20 : 2,
            width: 14,
            height: 14,
            borderRadius: '50%',
            background: enabled ? '#fff' : 'var(--text-muted)',
            transition: 'all 250ms cubic-bezier(0.4, 0, 0.2, 1)',
            boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
          }} />
        </button>
        {enabled && (
          <span style={{
            fontSize: 9,
            fontWeight: 700,
            color: 'var(--success)',
            textTransform: 'uppercase',
            letterSpacing: '1px',
          }}>
            ON
          </span>
        )}
      </div>

      {errorMsg && (
        <div style={{
          position: 'fixed', bottom: 20, right: 20,
          background: 'var(--danger-bg)', border: '1px solid var(--danger-border)',
          color: 'var(--danger)', padding: '10px 16px', borderRadius: 'var(--radius-md)',
          fontSize: 12, fontWeight: 500, zIndex: 9999, maxWidth: 320,
          boxShadow: 'var(--shadow-lg)',
        }} role="alert">
          {errorMsg}
        </div>
      )}
    </>
  );
};

export default AutopilotToggle;
