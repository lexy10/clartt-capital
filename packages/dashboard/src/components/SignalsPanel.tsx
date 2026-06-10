import { type FC, useState, useEffect } from 'react';
import { apiClient } from '../services/ApiClient';
import { wsManager } from '../services/WebSocketManager';
import type { Signal } from '../types/signal';

interface SignalsPanelProps {
  instrument: string;
}

const SignalsPanel: FC<SignalsPanelProps> = ({ instrument }) => {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    apiClient.signals.getRecent({ instrument, limit: 10 })
      .then((data) => { if (!cancelled) setSignals(data); })
      .catch(() => { if (!cancelled) setSignals([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [instrument]);

  useEffect(() => {
    const subId = wsManager.subscribe('signals', (signal: Signal) => {
      if (signal.instrument === instrument) {
        setSignals((prev) => [signal, ...prev].slice(0, 15));
      }
    });
    return () => { wsManager.unsubscribe(subId); };
  }, [instrument]);

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Signals</span>
        {signals.length > 0 && (
          <span className="badge badge-accent">{signals.length}</span>
        )}
      </div>

      {loading && <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>Loading…</p>}

      {!loading && signals.length === 0 && (
        <div className="empty-state">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8Z" />
          </svg>
          <span>No recent signals</span>
        </div>
      )}

      {signals.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 200, overflowY: 'auto' }}>
          {signals.map((sig) => (
            <div key={sig.id} className={`signal-card ${sig.direction === 'BUY' ? 'buy' : 'sell'}`}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                <span style={{
                  fontWeight: 700, fontSize: 10, letterSpacing: '0.5px',
                  color: sig.direction === 'BUY' ? 'var(--success)' : 'var(--danger)',
                }}>
                  {sig.direction}
                </span>
                <span style={{ color: 'var(--text-muted)', fontSize: 10, fontFamily: 'var(--font-mono)' }}>
                  {new Date(sig.created_at).toLocaleTimeString()}
                </span>
              </div>
              <div style={{
                display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 4,
                fontSize: 10, color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)',
              }}>
                <span>E {sig.entry_price.toFixed(1)}</span>
                <span>SL {sig.stop_loss.toFixed(1)}</span>
                <span>TP {sig.take_profit.toFixed(1)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default SignalsPanel;
