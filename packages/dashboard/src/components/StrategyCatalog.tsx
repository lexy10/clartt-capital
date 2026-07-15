import { type FC, useEffect, useState } from 'react';
import { apiClient } from '../services/ApiClient';
import type { Strategy, BacktestResult } from '../types/api';

/**
 * Trader-facing, read-only strategy catalogue. Shows what each strategy trades
 * (instruments, timeframes) and lets a trader run a backtest to verify its
 * performance — but never exposes the tuned config or algorithm source (the
 * backend strips those for non-admins).
 */
const StrategyCatalog: FC = () => {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiClient.strategies.list()
      .then((s) => setStrategies(s))
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load strategies'))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div style={{ padding: 16, fontFamily: 'var(--font-sans)', color: 'var(--text-primary)' }}>
      <h2 style={{ margin: 0, fontSize: 16 }}>Strategies</h2>
      <p style={{ margin: '2px 0 16px', fontSize: 12, color: 'var(--text-muted)' }}>
        Browse available strategies and run a backtest to verify their performance.
      </p>

      {error && <div style={errorBanner}>{error}</div>}
      {loading ? (
        <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>Loading strategies…</p>
      ) : strategies.length === 0 ? (
        <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>No strategies available.</p>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 12 }}>
          {strategies.map((s) => <StrategyCard key={s.id} strategy={s} />)}
        </div>
      )}
    </div>
  );
};

const StrategyCard: FC<{ strategy: Strategy }> = ({ strategy }) => {
  const cfg = strategy.config as Record<string, unknown>;
  const instruments = (Array.isArray(cfg.instruments) ? cfg.instruments : []) as string[];
  const entryTf = (cfg.entry_timeframe as string) || null;
  const trendTf = (cfg.trend_timeframe as string) || null;

  const [result, setResult] = useState<BacktestResult | null>(null);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Load the most recent stored backtest (if any) so the card shows metrics
  // without the trader having to run one first.
  useEffect(() => {
    apiClient.strategies.getBacktestResults(strategy.id)
      .then((rs) => { const done = rs.find((r) => r.status === 'completed'); if (done) setResult(done); })
      .catch(() => {});
  }, [strategy.id]);

  const runBacktest = async () => {
    setRunning(true);
    setErr(null);
    try {
      const end = new Date();
      const start = new Date();
      start.setMonth(start.getMonth() - 3); // last 3 months
      const res = await apiClient.strategies.runBacktest({
        strategy_id: strategy.id,
        instrument: instruments[0] || '',
        timeframe: entryTf || '',
        start_date: start.toISOString(),
        end_date: end.toISOString(),
        parameters: {}, // use the strategy's stored config as-is
      });
      setResult(res);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Backtest failed');
    }
    setRunning(false);
  };

  return (
    <div style={card}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>{strategy.name}</div>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>{strategy.algorithm}</div>
        </div>
        {strategy.enabled === false && <span style={disabledTag}>disabled</span>}
      </div>

      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 8 }}>
        {instruments.map((i) => <span key={i} style={chip}>{i}</span>)}
        {entryTf && <span style={{ ...chip, opacity: 0.7 }}>entry {entryTf}</span>}
        {trendTf && <span style={{ ...chip, opacity: 0.7 }}>trend {trendTf}</span>}
      </div>

      {/* Metrics */}
      <div style={{ marginTop: 12, borderTop: '1px solid var(--border-primary)', paddingTop: 10 }}>
        {result ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
            <Metric label="Win rate" value={pct(result.winRate)} tone={num(result.winRate) >= 50 ? 'good' : 'warn'} />
            <Metric label="Profit factor" value={fx(result.profitFactor)} tone={num(result.profitFactor) >= 1.5 ? 'good' : num(result.profitFactor) >= 1 ? 'warn' : 'bad'} />
            <Metric label="Trades" value={String(result.totalTrades ?? 0)} />
            <Metric label="Net" value={money(result.netProfit)} tone={num(result.netProfit) >= 0 ? 'good' : 'bad'} />
            <Metric label="Max DD" value={pct(result.maxDrawdown)} tone="warn" />
            <Metric label="Status" value={result.status ?? '—'} />
          </div>
        ) : (
          <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>No backtest yet — run one to see performance.</div>
        )}
      </div>

      {err && <div style={{ ...errorBanner, marginTop: 8, marginBottom: 0 }}>{err}</div>}

      <div style={{ marginTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
        <button onClick={runBacktest} disabled={running || !instruments.length} style={primaryBtn}
          title={!instruments.length ? 'Strategy has no instrument configured' : 'Backtest the last 3 months'}>
          {running ? 'Running…' : result ? 'Re-run Backtest' : 'Run Backtest'}
        </button>
      </div>
    </div>
  );
};

const Metric: FC<{ label: string; value: string; tone?: 'good' | 'warn' | 'bad' }> = ({ label, value, tone }) => (
  <div>
    <div style={{ fontSize: 9, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</div>
    <div style={{
      fontSize: 13, fontFamily: 'var(--font-mono)', fontWeight: 600,
      color: tone === 'good' ? 'var(--success)' : tone === 'bad' ? 'var(--danger)' : tone === 'warn' ? 'var(--warning)' : 'var(--text-primary)',
    }}>{value}</div>
  </div>
);

const num = (v: number | null | undefined) => (typeof v === 'number' ? v : 0);
const pct = (v: number | null | undefined) => (typeof v === 'number' ? `${v.toFixed(1)}%` : '—');
const fx = (v: number | null | undefined) => (typeof v === 'number' ? v.toFixed(2) : '—');
const money = (v: number | null | undefined) => (typeof v === 'number' ? `${v >= 0 ? '+' : ''}$${Math.abs(v).toFixed(2)}` : '—');

const card: React.CSSProperties = { background: 'var(--bg-secondary)', border: '1px solid var(--glass-border)', borderRadius: 'var(--radius-md)', padding: 14 };
const chip: React.CSSProperties = { fontSize: 9, padding: '1px 6px', borderRadius: 3, background: 'var(--bg-surface)', border: '1px solid var(--glass-border)', color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' };
const disabledTag: React.CSSProperties = { fontSize: 9, color: 'var(--text-muted)', border: '1px solid var(--glass-border)', borderRadius: 3, padding: '1px 5px' };
const primaryBtn: React.CSSProperties = { background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)', padding: '6px 14px', fontSize: 12, fontWeight: 600, cursor: 'pointer' };
const errorBanner: React.CSSProperties = { background: 'var(--danger-bg, rgba(239,68,68,0.1))', border: '1px solid var(--danger)', borderRadius: 'var(--radius-sm)', padding: '8px 12px', marginBottom: 12, fontSize: 11, color: 'var(--danger)' };

export default StrategyCatalog;
