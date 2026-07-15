import { type FC, useEffect, useState } from 'react';
import { apiClient } from '../services/ApiClient';
import type { AlgorithmInfo } from '../types/api';

/**
 * Trader-facing, read-only algorithm list. Shows each algorithm's name and
 * description only — the source code and tunable parameters are admin-only
 * (the backend strips them for non-admins).
 */
const AlgorithmCatalog: FC = () => {
  const [algorithms, setAlgorithms] = useState<Pick<AlgorithmInfo, 'name' | 'description'>[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiClient.strategies.getAlgorithms()
      .then((a) => setAlgorithms(a))
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load algorithms'))
      .finally(() => setLoading(false));
  }, []);

  const pretty = (name: string) =>
    name.split('_').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');

  return (
    <div style={{ padding: 16, fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', maxWidth: 720 }}>
      <h2 style={{ margin: 0, fontSize: 16 }}>Algorithms</h2>
      <p style={{ margin: '2px 0 16px', fontSize: 12, color: 'var(--text-muted)' }}>
        The trading logic available to strategies.
      </p>

      {error && (
        <div style={{ background: 'var(--danger-bg, rgba(239,68,68,0.1))', border: '1px solid var(--danger)', borderRadius: 'var(--radius-sm)', padding: '8px 12px', fontSize: 11, color: 'var(--danger)' }}>{error}</div>
      )}
      {loading ? (
        <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>Loading…</p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {algorithms.map((a) => (
            <div key={a.name} style={{ background: 'var(--bg-secondary)', border: '1px solid var(--glass-border)', borderRadius: 'var(--radius-md)', padding: 14 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>{pretty(a.name)}</div>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', marginBottom: 6 }}>{a.name}</div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                {a.description || 'No description provided.'}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default AlgorithmCatalog;
