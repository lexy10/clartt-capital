import { type FC, Fragment, useEffect, useRef } from 'react';
import { usePerformanceStore } from '../../stores/performanceStore';
import Skeleton from '../Skeleton';

const moneyFmt = new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', maximumFractionDigits: 2,
});

function fmtMoney(v: number): string { return moneyFmt.format(v || 0); }
function fmtPct(v: number): string { return `${v.toFixed(2)}%`; }
function fmtNum(v: number): string { return v.toLocaleString('en-US', { maximumFractionDigits: 2 }); }

type Tone = 'success' | 'warning' | 'danger' | 'muted';
function pnlTone(v: number): Tone {
  if (v > 0) return 'success';
  if (v < 0) return 'danger';
  return 'muted';
}

const Tile: FC<{ label: string; value: string; tone?: Tone; caption?: string }> = ({ label, value, tone = 'muted', caption }) => {
  const colorByTone: Record<Tone, string> = {
    success: 'var(--success)',
    warning: 'var(--warning)',
    danger: 'var(--danger)',
    muted: 'var(--text-primary)',
  };
  return (
    <div style={{
      background: 'var(--bg-surface)',
      border: '1px solid var(--border-primary)',
      borderRadius: 'var(--radius-md)',
      padding: '12px 16px',
      minWidth: 0,
    }}>
      <div style={{ fontSize: 10, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 18, fontFamily: 'var(--font-mono)', fontWeight: 600, color: colorByTone[tone], marginTop: 4 }}>
        {value}
      </div>
      {caption && (
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>{caption}</div>
      )}
    </div>
  );
};

const BROKER_LABEL: Record<string, string> = {
  deriv: 'Deriv',
  metaapi: 'MetaAPI (MT5)',
  alpaca: 'Alpaca',
  binance: 'Binance',
  ibkr: 'IBKR',
  stub: 'Demo Stub',
};

const REFRESH_OPTIONS = [
  { label: 'Off', ms: 0 },
  { label: 'Every 30s', ms: 30_000 },
  { label: 'Every 1 min', ms: 60_000 },
  { label: 'Every 5 min', ms: 300_000 },
];

/** Cell descriptor for the dense table layout used by By Broker / Top Performing. */
type CompactCell = {
  value: string;
  mono?: boolean;
  bold?: boolean;
  color?: string;
  size?: 'sm';
};

type CompactRow = {
  key: string;
  cells: CompactCell[];
  /** Optional small footer line under the row — e.g. "Top: R_25". */
  footer?: { label: string; value: string };
};

/** Compact, table-styled card used for By Broker + Top Performing.
 *  Designed to fit two side-by-side without horizontal scroll. */
const CompactTable: FC<{
  title: string;
  columns: string[];
  rows: CompactRow[];
}> = ({ title, columns, rows }) => (
  <div style={{
    background: 'var(--bg-surface)',
    border: '1px solid var(--border-primary)',
    borderRadius: 'var(--radius-md)',
    padding: '10px 12px',
  }}>
    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>
      {title}
    </div>
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
      <thead>
        <tr style={{ color: 'var(--text-muted)' }}>
          {columns.map((col, idx) => (
            <th key={col} style={{
              textAlign: idx === 0 ? 'left' : 'right',
              fontWeight: 500,
              fontSize: 9,
              textTransform: 'uppercase',
              letterSpacing: 0.5,
              padding: '4px 6px',
              borderBottom: '1px solid var(--border-primary)',
            }}>
              {col}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <Fragment key={row.key}>
            <tr>
              {row.cells.map((cell, idx) => (
                <td key={idx} style={{
                  textAlign: idx === 0 ? 'left' : 'right',
                  padding: '6px 6px',
                  fontFamily: cell.mono ? 'var(--font-mono)' : undefined,
                  fontWeight: cell.bold ? 600 : undefined,
                  color: cell.color ?? 'var(--text-primary)',
                  fontSize: cell.size === 'sm' ? 10 : 11,
                  whiteSpace: 'nowrap',
                }}>
                  {cell.value}
                </td>
              ))}
            </tr>
            {row.footer && (
              <tr>
                <td colSpan={row.cells.length} style={{
                  fontSize: 10,
                  color: 'var(--text-muted)',
                  padding: '0 6px 6px',
                }}>
                  {row.footer.label}: <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{row.footer.value}</span>
                </td>
              </tr>
            )}
          </Fragment>
        ))}
      </tbody>
    </table>
  </div>
);

/**
 * Multi-account, multi-broker summary card for the Live Desk.
 *
 * Shows totals (equity, today's P&L, open positions, trade count) and
 * breaks them down per broker. Auto-refreshes on a user-selectable interval.
 */
const MultiAccountSummary: FC = () => {
  const overview = usePerformanceStore((s) => s.overview);
  const fetchOverview = usePerformanceStore((s) => s.fetchOverview);
  const overviewLoading = usePerformanceStore((s) => s.overviewLoading);
  const fetchAccounts = usePerformanceStore((s) => s.fetchAccounts);
  const refreshIntervalMs = usePerformanceStore((s) => s.overviewRefreshMs);
  const setRefreshInterval = usePerformanceStore((s) => s.setOverviewRefreshMs);

  // Auto-refresh loop
  const timerRef = useRef<number | null>(null);
  useEffect(() => {
    if (timerRef.current) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (refreshIntervalMs > 0) {
      timerRef.current = window.setInterval(() => {
        // Silent — swap numbers in place; never flash skeletons on auto-refresh.
        fetchOverview(true);
        fetchAccounts(true);
      }, refreshIntervalMs);
    }
    return () => {
      if (timerRef.current) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [refreshIntervalMs, fetchOverview, fetchAccounts]);

  const totalEquity = overview?.totalEquity ?? 0;
  const totalBalance = overview?.totalBalance ?? 0;
  const todayPnl = overview?.todayPnl ?? 0;
  const totalTrades = overview?.totalTrades ?? 0;
  const accountsCount = overview?.accountsCount ?? 0;
  const openPositionsCount = overview?.openPositionsCount ?? 0;
  const totalExposure = overview?.totalExposure ?? 0;
  const winRate = overview?.winRate ?? 0;
  const profitFactor = overview?.profitFactor ?? 0;
  const maxDrawdown = overview?.maxDrawdown ?? 0;
  const periodPercentChange = overview?.periodPercentChange ?? 0;
  const byBroker = overview?.byBroker ?? [];
  const topInstruments = overview?.topInstruments ?? [];

  // First-load skeleton — only on the truly initial fetch. Once overview
  // has loaded once, subsequent refreshes never blink back to skeletons
  // (sticky stale-while-revalidate via hasSeenOverviewRef).
  const hasSeenOverviewRef = useRef(overview !== null);
  if (overview !== null) {
    hasSeenOverviewRef.current = true;
  }
  if (overview === null && !hasSeenOverviewRef.current) {
    return <MultiAccountSummarySkeleton />;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Header with auto-refresh control */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 4px' }}>
        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          {accountsCount} account{accountsCount === 1 ? '' : 's'} across {byBroker.length} broker{byBroker.length === 1 ? '' : 's'}
          {overviewLoading && <span style={{ marginLeft: 8, fontStyle: 'italic' }}>· refreshing…</span>}
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Auto-refresh:</span>
          <select
            value={refreshIntervalMs}
            onChange={(e) => setRefreshInterval(Number(e.target.value))}
            style={{
              background: 'var(--bg-surface)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border-primary)',
              borderRadius: 'var(--radius-sm)',
              fontSize: 11,
              padding: '4px 8px',
            }}
          >
            {REFRESH_OPTIONS.map((opt) => (
              <option key={opt.ms} value={opt.ms}>{opt.label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Top row: totals */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10 }}>
        <Tile label="Total Equity" value={fmtMoney(totalEquity)} caption={`Balance ${fmtMoney(totalBalance)}`} />
        <Tile label="Period P&L" value={`${todayPnl >= 0 ? '+' : ''}${fmtMoney(todayPnl)}`} tone={pnlTone(todayPnl)} caption={fmtPct(periodPercentChange)} />
        <Tile label="Open Positions" value={String(openPositionsCount)} tone={openPositionsCount > 0 ? 'warning' : 'muted'} caption={openPositionsCount > 0 ? `${fmtNum(totalExposure)} lots exposure` : 'No exposure'} />
        <Tile label="Closed Trades" value={String(totalTrades)} caption={totalTrades > 0 ? 'In selected period' : 'None yet'} />
      </div>

      {/* Performance row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10 }}>
        <Tile label="Win Rate" value={fmtPct(winRate)} tone={winRate >= 50 ? 'success' : winRate > 0 ? 'warning' : 'muted'} />
        <Tile label="Profit Factor" value={profitFactor.toFixed(2)} tone={profitFactor > 1.5 ? 'success' : profitFactor > 1 ? 'warning' : profitFactor === 0 ? 'muted' : 'danger'} />
        <Tile label="Avg R:R" value={(overview?.avgRiskReward ?? 0).toFixed(2)} />
        <Tile label="Max Drawdown" value={fmtPct(maxDrawdown)} tone={maxDrawdown >= 20 ? 'danger' : maxDrawdown >= 10 ? 'warning' : 'muted'} />
      </div>

      {/* By Broker + Top Performing — side by side, compact tables */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))',
        gap: 12,
      }}>
        {byBroker.length > 0 && (
          <CompactTable
            title="By broker"
            columns={['Broker', 'Equity', 'P&L', '%', 'Trades', 'WR', 'Open']}
            rows={byBroker.map((b) => {
              const pnlPct = b.totalBalance > 0 ? (b.periodPnl / b.totalBalance) * 100 : 0;
              return {
                key: b.provider,
                cells: [
                  { value: BROKER_LABEL[b.provider] || b.provider, bold: true },
                  { value: fmtMoney(b.totalEquity), mono: true },
                  {
                    value: `${b.periodPnl >= 0 ? '+' : ''}${fmtMoney(b.periodPnl)}`,
                    mono: true,
                    color: b.periodPnl >= 0 ? 'var(--success)' : 'var(--danger)',
                  },
                  {
                    value: `${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`,
                    mono: true,
                    color: pnlPct >= 0 ? 'var(--success)' : 'var(--danger)',
                    size: 'sm',
                  },
                  { value: String(b.totalTrades ?? 0), mono: true },
                  {
                    value: b.winRate != null ? `${b.winRate.toFixed(0)}%` : '—',
                    mono: true,
                    color: b.winRate != null && b.winRate >= 50 ? 'var(--success)'
                          : b.winRate != null && b.winRate > 0 ? 'var(--warning)'
                          : undefined,
                  },
                  { value: String(b.openPositions), mono: true },
                ],
                footer: b.topInstrument
                  ? { label: 'Top', value: b.topInstrument }
                  : undefined,
              };
            })}
          />
        )}

        {topInstruments.length > 0 && (
          <CompactTable
            title="Top performing (period)"
            columns={['Instrument', 'P&L', 'Trades', 'WR', 'Avg']}
            rows={topInstruments.map((i) => {
              const winRate = i.winRate ?? 0;
              const avgPnl = i.tradeCount > 0 ? i.totalPnl / i.tradeCount : 0;
              return {
                key: i.instrument,
                cells: [
                  { value: i.instrument, bold: true },
                  {
                    value: `${i.totalPnl >= 0 ? '+' : ''}${fmtMoney(i.totalPnl)}`,
                    mono: true,
                    color: i.totalPnl >= 0 ? 'var(--success)' : 'var(--danger)',
                  },
                  { value: String(i.tradeCount), mono: true },
                  {
                    value: `${winRate.toFixed(0)}%`,
                    mono: true,
                    color: winRate >= 50 ? 'var(--success)' : winRate > 0 ? 'var(--warning)' : undefined,
                  },
                  {
                    value: `${avgPnl >= 0 ? '+' : ''}${fmtMoney(avgPnl)}`,
                    mono: true,
                    color: avgPnl >= 0 ? 'var(--success)' : 'var(--danger)',
                    size: 'sm',
                  },
                ],
              };
            })}
          />
        )}
      </div>
    </div>
  );
};

/** Shown on first load — mirrors the live layout so the page doesn't jump
 *  when real data arrives. Two rows of 4 tiles + two compact tables. */
const MultiAccountSummarySkeleton: FC = () => {
  const Tile = (
    <div style={{
      background: 'var(--bg-surface)',
      border: '1px solid var(--border-primary)',
      borderRadius: 'var(--radius-md)',
      padding: '12px 16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 8,
      minHeight: 70,
    }}>
      <Skeleton width={70} height={9} />
      <Skeleton width="80%" height={18} />
      <Skeleton width="50%" height={9} />
    </div>
  );
  const Table = (
    <div style={{
      background: 'var(--bg-surface)',
      border: '1px solid var(--border-primary)',
      borderRadius: 'var(--radius-md)',
      padding: '10px 12px',
      display: 'flex',
      flexDirection: 'column',
      gap: 8,
    }}>
      <Skeleton width={90} height={9} />
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} height={14} />
      ))}
    </div>
  );
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0 4px' }}>
        <Skeleton width={180} height={12} />
        <Skeleton width={120} height={20} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10 }}>
        {[0, 1, 2, 3].map((i) => <Fragment key={i}>{Tile}</Fragment>)}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10 }}>
        {[0, 1, 2, 3].map((i) => <Fragment key={i}>{Tile}</Fragment>)}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 12 }}>
        {Table}
        {Table}
      </div>
    </div>
  );
};

export default MultiAccountSummary;
