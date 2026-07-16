import { type FC, type ReactNode, useEffect, useRef } from 'react';
import { useSignalStore } from '../stores/signalStore';
import type { Signal, SignalDirection } from '../types/signal';
import { signalExecution, signalStrategyName } from '../types/signal';
import type { Timeframe } from '../types/timeframe';
import { wsManager } from '../services/WebSocketManager';
import SignalStatusBadge from './SignalStatusBadge';

/* ── Pure helpers (exported for testing) ── */

export function getConfidenceColor(score: number): 'green' | 'yellow' | 'red' {
  if (score > 0.7) return 'green';
  if (score >= 0.4) return 'yellow';
  return 'red';
}

export function getRiskReward(direction: SignalDirection, entry: number, sl: number, tp: number): number {
  if (direction === 'BUY') return (tp - entry) / (entry - sl);
  return (entry - tp) / (sl - entry);
}

export function getSlDistance(entry: number, sl: number): number {
  return Math.abs(entry - sl);
}

export function formatPrice(value: number): string {
  return value.toFixed(2);
}

/* ── Color maps ── */

const confidenceColorMap: Record<'green' | 'yellow' | 'red', string> = {
  green: 'var(--success)',
  yellow: 'var(--warning)',
  red: 'var(--danger)',
};

const TIMEFRAMES: Timeframe[] = ['1m', '5m', '15m', '30m', '1h', '4h', '1d'];

/* ── Component ── */

const SignalsPage: FC = () => {
  const {
    filteredSignals, loading, error, total, offset, limit, filters, sortBy,
    selectedSignal, signals,
    fetchSignals, setFilter, setSort, nextPage, prevPage, selectSignal, addSignal,
  } = useSignalStore();

  const subIdRef = useRef<string | null>(null);

  // Fetch on mount
  useEffect(() => {
    fetchSignals();
  }, [fetchSignals]);

  // WebSocket subscribe/unsubscribe
  useEffect(() => {
    subIdRef.current = wsManager.subscribe('signals', (signal) => {
      addSignal(signal);
    });
    return () => {
      if (subIdRef.current) {
        wsManager.unsubscribe(subIdRef.current);
        subIdRef.current = null;
      }
    };
  }, [addSignal]);

  const filtersActive = filters.direction !== 'ALL' || filters.mode !== 'ALL' || filters.timeframe !== 'ALL';
  const prevDisabled = offset === 0;
  const nextDisabled = offset + limit >= total;

  /* ── Detail panel ── */
  if (selectedSignal) {
    return (
      <div style={{ padding: 24, fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-primary)', minHeight: '100%' }}>
        <button onClick={() => selectSignal(null)} style={backBtnStyle} aria-label="Close detail panel">
          ← Back to Signals
        </button>

        <h2 style={{ fontSize: 18, fontWeight: 600, margin: '16px 0 20px' }}>Signal Detail</h2>

        <div style={{ background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)', padding: 20 }}>
          <DetailRow label="Instrument" value={selectedSignal.instrument} />
          <DetailRow label="Direction">
            <DirectionBadge direction={selectedSignal.direction} />
          </DetailRow>
          <DetailRow label="Entry Price" value={formatPrice(selectedSignal.entry_price)} mono />
          <DetailRow label="Stop Loss" value={formatPrice(selectedSignal.stop_loss)} mono />
          <DetailRow label="Take Profit" value={formatPrice(selectedSignal.take_profit)} mono />
          <DetailRow label="Position Size" value={String(selectedSignal.position_size)} mono />
          <DetailRow label="Confidence Score">
            <ConfidenceBadge score={selectedSignal.confidence_score} />
          </DetailRow>
          <DetailRow label="Risk:Reward" value={formatRR(selectedSignal)} mono />
          <DetailRow label="SL Distance" value={formatPrice(getSlDistance(selectedSignal.entry_price, selectedSignal.stop_loss))} mono />
          <DetailRow label="Timeframe" value={selectedSignal.timeframe} />
          <DetailRow label="Mode" value={selectedSignal.mode} />
          <DetailRow label="Strategy" value={signalStrategyName(selectedSignal)} />
          <DetailRow label="Execution">
            <SignalStatusBadge signal={selectedSignal} />
          </DetailRow>
          <DetailRow label="Why" value={signalExecution(selectedSignal).reason} />
          <DetailRow label="Created" value={fmtDate(selectedSignal.created_at)} />

          {/* Metadata */}
          <h3 style={{ fontSize: 13, fontWeight: 600, margin: '20px 0 10px', color: 'var(--text-secondary)' }}>Metadata</h3>
          {selectedSignal.metadata ? (
            <>
              <DetailRow label="BOS Type" value={selectedSignal.metadata.bos_type ?? '—'} />
              <DetailRow label="Liquidity Swept" value={selectedSignal.metadata.liquidity_swept ? 'Yes' : 'No'} />
              <DetailRow label="Session" value={selectedSignal.metadata.session ?? '—'} />
              <DetailRow label="Spread" value={selectedSignal.metadata.spread_at_generation != null ? formatPrice(selectedSignal.metadata.spread_at_generation) : '—'} mono />
              <DetailRow label="Volatility Ratio" value={selectedSignal.metadata.volatility_ratio != null ? selectedSignal.metadata.volatility_ratio.toFixed(4) : '—'} mono />
            </>
          ) : (
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', padding: '6px 0' }}>No metadata available</div>
          )}
        </div>
      </div>
    );
  }

  /* ── Main list view ── */
  return (
    <div style={{ padding: 24, fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-primary)', minHeight: '100%' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>Signals</h1>
        <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
          {filtersActive
            ? <span>{filteredSignals.length} filtered / {total} total</span>
            : <span>{total} total</span>}
        </div>
      </div>

      {/* Filter bar */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        <FilterSelect
          label="Direction"
          value={filters.direction}
          options={['ALL', 'BUY', 'SELL']}
          onChange={(v) => setFilter('direction', v as SignalDirection | 'ALL')}
        />
        <FilterSelect
          label="Mode"
          value={filters.mode}
          options={['ALL', 'backtest', 'forward_test', 'live']}
          onChange={(v) => setFilter('mode', v as 'ALL' | 'backtest' | 'forward_test' | 'live')}
        />
        <FilterSelect
          label="Timeframe"
          value={filters.timeframe}
          options={['ALL', ...TIMEFRAMES]}
          onChange={(v) => setFilter('timeframe', v as Timeframe | 'ALL')}
        />

        {/* Sort controls */}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          {(['created_at', 'confidence_score', 'entry_price'] as const).map((field) => (
            <button
              key={field}
              onClick={() => setSort(field)}
              style={{
                ...sortBtnStyle,
                background: sortBy.field === field ? 'var(--accent-dim)' : 'transparent',
                color: sortBy.field === field ? 'var(--accent)' : 'var(--text-secondary)',
              }}
            >
              {sortLabels[field]} {sortBy.field === field ? (sortBy.order === 'asc' ? '↑' : '↓') : ''}
            </button>
          ))}
        </div>
      </div>

      {/* Error state */}
      {error && (
        <div style={errorBannerStyle} role="alert">
          <span>{error}</span>
          <button onClick={fetchSignals} style={retryBtnStyle}>Retry</button>
        </div>
      )}

      {/* Loading state */}
      {loading && signals.length === 0 && (
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', padding: 16 }}>Loading signals…</div>
      )}

      {/* Empty state */}
      {!loading && !error && signals.length === 0 && (
        <div style={{
          textAlign: 'center', padding: 48, color: 'var(--text-secondary)', fontSize: 14,
          background: 'var(--bg-surface)', borderRadius: 'var(--radius-sm)',
        }}>
          No signals found
        </div>
      )}

      {/* Table */}
      {filteredSignals.length > 0 && (
        <div style={{ background: 'var(--bg-surface)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr>
                  {TABLE_HEADERS.map((h) => (
                    <th key={h.label} style={{ ...thStyle, textAlign: h.align }}>{h.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredSignals.map((sig) => (
                  <tr
                    key={sig.id}
                    onClick={() => selectSignal(sig)}
                    style={{ borderBottom: '1px solid var(--bg-primary)', cursor: 'pointer' }}
                  >
                    <td style={tdLeft}>{sig.instrument}</td>
                    <td style={{ ...tdLeft, fontFamily: 'var(--font-sans)' }}>{signalStrategyName(sig)}</td>
                    <td style={tdLeft}><DirectionBadge direction={sig.direction} /></td>
                    <td style={tdRight}>{formatPrice(sig.entry_price)}</td>
                    <td style={tdRight}>{formatPrice(sig.stop_loss)}</td>
                    <td style={tdRight}>{formatPrice(sig.take_profit)}</td>
                    <td style={tdRight}><ConfidenceBadge score={sig.confidence_score} /></td>
                    <td style={tdRight}>{formatPrice(getRiskReward(sig.direction, sig.entry_price, sig.stop_loss, sig.take_profit))}</td>
                    <td style={tdLeft}>{sig.timeframe}</td>
                    <td style={tdLeft}><SignalStatusBadge signal={sig} /></td>
                    <td style={{ ...tdLeft, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>{fmtDate(sig.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Pagination */}
      <div style={{ display: 'flex', justifyContent: 'center', gap: 12, marginTop: 16 }}>
        <button onClick={prevPage} disabled={prevDisabled} style={paginationBtnStyle(prevDisabled)}>
          ← Prev
        </button>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: '32px' }}>
          Page {Math.floor(offset / limit) + 1}
        </span>
        <button onClick={nextPage} disabled={nextDisabled} style={paginationBtnStyle(nextDisabled)}>
          Next →
        </button>
      </div>
    </div>
  );
};


/* ── Sub-components ── */

const DirectionBadge: FC<{ direction: SignalDirection }> = ({ direction }) => (
  <span style={{
    display: 'inline-block',
    padding: '2px 8px',
    borderRadius: 'var(--radius-sm)',
    fontSize: 11,
    fontWeight: 600,
    color: '#fff',
    background: direction === 'BUY' ? 'var(--success)' : 'var(--danger)',
  }}>
    {direction}
  </span>
);

const ConfidenceBadge: FC<{ score: number }> = ({ score }) => {
  const color = confidenceColorMap[getConfidenceColor(score)];
  return (
    <span style={{ fontFamily: 'var(--font-mono)', color }}>
      {score.toFixed(2)}
    </span>
  );
};

const DetailRow: FC<{ label: string; value?: string; mono?: boolean; children?: ReactNode }> = ({ label, value, mono, children }) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid var(--border-primary)' }}>
    <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{label}</span>
    {children ?? (
      <span style={{ fontSize: 12, fontFamily: mono ? 'var(--font-mono)' : 'var(--font-sans)', color: 'var(--text-primary)' }}>
        {value}
      </span>
    )}
  </div>
);

interface FilterSelectProps {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}

const FilterSelect: FC<FilterSelectProps> = ({ label, value, options, onChange }) => (
  <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--text-secondary)' }}>
    {label}:
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={selectStyle}
      aria-label={`Filter by ${label}`}
    >
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  </label>
);

/* ── Helpers ── */

function formatRR(sig: Signal): string {
  const rr = getRiskReward(sig.direction, sig.entry_price, sig.stop_loss, sig.take_profit);
  return isFinite(rr) ? rr.toFixed(2) : '—';
}

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
    ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
}

const sortLabels: Record<string, string> = {
  created_at: 'Date',
  confidence_score: 'Confidence',
  entry_price: 'Price',
};

/* ── Table config ── */

const TABLE_HEADERS = [
  { label: 'Instrument', align: 'left' as const },
  { label: 'Strategy', align: 'left' as const },
  { label: 'Direction', align: 'left' as const },
  { label: 'Entry', align: 'right' as const },
  { label: 'SL', align: 'right' as const },
  { label: 'TP', align: 'right' as const },
  { label: 'Confidence', align: 'right' as const },
  { label: 'R:R', align: 'right' as const },
  { label: 'TF', align: 'left' as const },
  { label: 'Status', align: 'left' as const },
  { label: 'Created', align: 'left' as const },
];

/* ── Styles ── */

const thStyle: React.CSSProperties = {
  padding: '10px 12px',
  fontFamily: 'var(--font-sans)',
  fontWeight: 500,
  color: 'var(--text-secondary)',
  borderBottom: '1px solid var(--text-secondary)',
  whiteSpace: 'nowrap',
  fontSize: 11,
};

const tdBase: React.CSSProperties = {
  padding: '8px 12px',
  fontFamily: 'var(--font-mono)',
  color: 'var(--text-primary)',
};

const tdLeft: React.CSSProperties = { ...tdBase, textAlign: 'left' };
const tdRight: React.CSSProperties = { ...tdBase, textAlign: 'right' };

const backBtnStyle: React.CSSProperties = {
  background: 'none',
  border: '1px solid var(--border-primary)',
  borderRadius: 'var(--radius-sm)',
  color: 'var(--text-secondary)',
  padding: '6px 12px',
  fontSize: 12,
  cursor: 'pointer',
};

const errorBannerStyle: React.CSSProperties = {
  background: 'var(--danger-bg)',
  border: '1px solid var(--danger)',
  borderRadius: 'var(--radius-md)',
  padding: '8px 12px',
  marginBottom: 12,
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  fontSize: 12,
  color: 'var(--danger)',
};

const retryBtnStyle: React.CSSProperties = {
  background: 'none',
  border: '1px solid var(--danger)',
  borderRadius: 'var(--radius-sm)',
  color: 'var(--danger)',
  padding: '4px 10px',
  fontSize: 11,
  cursor: 'pointer',
};

const sortBtnStyle: React.CSSProperties = {
  border: '1px solid var(--border-primary)',
  borderRadius: 'var(--radius-sm)',
  padding: '4px 10px',
  fontSize: 11,
  cursor: 'pointer',
};

const selectStyle: React.CSSProperties = {
  background: 'var(--bg-surface)',
  color: 'var(--text-primary)',
  border: '1px solid var(--border-primary)',
  borderRadius: 'var(--radius-sm)',
  padding: '4px 8px',
  fontSize: 12,
  cursor: 'pointer',
};

const paginationBtnStyle = (disabled: boolean): React.CSSProperties => ({
  background: disabled ? 'var(--bg-surface)' : 'var(--accent-dim)',
  color: disabled ? 'var(--text-muted)' : 'var(--accent)',
  border: '1px solid var(--border-primary)',
  borderRadius: 'var(--radius-sm)',
  padding: '6px 16px',
  fontSize: 12,
  cursor: disabled ? 'default' : 'pointer',
  opacity: disabled ? 0.5 : 1,
});

export default SignalsPage;
