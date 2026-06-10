import { type FC, useState, useEffect } from 'react';
import { apiClient } from '../services/ApiClient';
import { wsManager } from '../services/WebSocketManager';
import type { Signal } from '../types/signal';

type TabId = 'overview' | 'technical' | 'signals';

const TABS: { id: TabId; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'technical', label: 'Technical' },
  { id: 'signals', label: 'Signals' },
];

interface InstrumentTabsProps {
  instrument: string;
}

const InstrumentTabs: FC<InstrumentTabsProps> = ({ instrument }) => {
  const [activeTab, setActiveTab] = useState<TabId>('overview');

  return (
    <div>
      <div style={{ display: 'flex', gap: 2, marginBottom: 12 }}>
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              padding: '6px 12px',
              fontSize: 11,
              fontWeight: activeTab === tab.id ? 600 : 400,
              color: activeTab === tab.id ? 'var(--accent)' : 'var(--text-muted)',
              background: activeTab === tab.id ? 'var(--accent-dim)' : 'transparent',
              border: 'none',
              borderRadius: 'var(--radius-sm)',
              cursor: 'pointer',
              transition: 'all var(--transition-fast)',
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'overview' && <OverviewTab instrument={instrument} />}
      {activeTab === 'technical' && <TechnicalTab />}
      {activeTab === 'signals' && <SignalsTab instrument={instrument} />}
    </div>
  );
};

const OverviewTab: FC<{ instrument: string }> = ({ instrument }) => (
  <div className="card">
    <div className="card-header">
      <span className="card-title">{instrument}</span>
      <span className="badge badge-accent">Index CFD</span>
    </div>
    <p style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.6, marginBottom: 12 }}>
      Dow Jones Industrial Average — 30 major US companies.
    </p>
    <div className="stats-grid">
      <div className="stat-item">
        <span className="stat-label">Exchange</span>
        <span style={{ fontSize: 13, color: 'var(--text-primary)' }}>CME</span>
      </div>
      <div className="stat-item">
        <span className="stat-label">Currency</span>
        <span style={{ fontSize: 13, color: 'var(--text-primary)' }}>USD</span>
      </div>
      <div className="stat-item">
        <span className="stat-label">Session</span>
        <span style={{ fontSize: 13, color: 'var(--text-primary)' }}>London / NY</span>
      </div>
      <div className="stat-item">
        <span className="stat-label">Spread</span>
        <span style={{ fontSize: 13, color: 'var(--text-primary)' }}>Variable</span>
      </div>
    </div>
  </div>
);

const TechnicalTab: FC = () => (
  <div className="card">
    <div className="empty-state">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M12 20V10M18 20V4M6 20v-4" />
      </svg>
      <span>Technical analysis coming soon</span>
    </div>
  </div>
);

const SignalsTab: FC<{ instrument: string }> = ({ instrument }) => {
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
        setSignals((prev) => [signal, ...prev].slice(0, 20));
      }
    });
    return () => { wsManager.unsubscribe(subId); };
  }, [instrument]);

  if (loading) return <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>Loading signals…</p>;

  if (signals.length === 0) {
    return (
      <div className="card">
        <div className="empty-state">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8Z" />
          </svg>
          <span>No recent signals</span>
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {signals.map((sig) => (
        <div key={sig.id} className={`signal-card ${sig.direction === 'BUY' ? 'buy' : 'sell'}`}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <span style={{ fontWeight: 600, fontSize: 12, color: sig.direction === 'BUY' ? 'var(--success)' : 'var(--danger)' }}>
              {sig.direction}
            </span>
            <span style={{ color: 'var(--text-muted)', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
              {new Date(sig.created_at).toLocaleTimeString()}
            </span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 4, fontSize: 11, color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
            <span>E: {sig.entry_price.toFixed(2)}</span>
            <span>SL: {sig.stop_loss.toFixed(2)}</span>
            <span>TP: {sig.take_profit.toFixed(2)}</span>
          </div>
        </div>
      ))}
    </div>
  );
};

export default InstrumentTabs;
