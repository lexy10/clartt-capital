import { type FC } from 'react';
import type { AccountPerformanceData, SparklinePoint } from '../../types/api';
import type { Position } from '../../types/trade';

interface AccountPerformanceCardProps {
  data: AccountPerformanceData;
  /** Currently-open positions on this account. Rendered as a "Running"
   *  section above the period stats so the user sees live trades alongside
   *  the period summary on a single card. */
  openPositions?: Position[];
  onDrillDown: (accountId: string) => void;
}

const BROKER_LABEL: Record<string, string> = {
  deriv: 'Deriv',
  metaapi: 'MetaAPI (MT5)',
  alpaca: 'Alpaca',
  binance: 'Binance',
  ibkr: 'IBKR',
  stub: 'Demo',
};

function formatCurrency(value: number): string {
  return `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatPercent(value: number): string {
  const sign = value >= 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

function pnlColor(value: number): string | undefined {
  if (value > 0) return 'var(--success)';
  if (value < 0) return 'var(--danger)';
  return undefined;
}

function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '—';
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return '—';
  const sec = Math.floor((Date.now() - ts) / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

function buildSparklinePath(points: SparklinePoint[], width: number, height: number): string {
  if (points.length < 2) return '';
  const equities = points.map((p) => p.equity);
  const min = Math.min(...equities);
  const max = Math.max(...equities);
  const range = max - min || 1;
  const padding = 2;
  const drawHeight = height - padding * 2;
  const stepX = width / (points.length - 1);

  return points
    .map((p, i) => {
      const x = i * stepX;
      const y = padding + drawHeight - ((p.equity - min) / range) * drawHeight;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}

function sparklineColor(points: SparklinePoint[]): string {
  if (points.length < 2) return 'var(--text-secondary)';
  return points[points.length - 1].equity >= points[0].equity
    ? 'var(--success)'
    : 'var(--danger)';
}

const Sparkline: FC<{ points: SparklinePoint[] }> = ({ points }) => {
  const width = 100;
  const height = 28;

  if (points.length < 2) {
    return (
      <svg width={width} height={height} aria-hidden="true">
        <line
          x1={0}
          y1={height / 2}
          x2={width}
          y2={height / 2}
          stroke="var(--text-secondary)"
          strokeWidth={1}
          strokeDasharray="4 2"
        />
      </svg>
    );
  }

  const path = buildSparklinePath(points, width, height);
  const color = sparklineColor(points);

  return (
    <svg width={width} height={height} aria-hidden="true">
      <polyline
        points={path}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
};

/** Compact, information-dense per-account card.
 *  Shows: broker + kind + autopilot status, balance/equity, period P&L,
 *  open positions, trade count + win rate, instruments traded, last activity. */
const AccountPerformanceCard: FC<AccountPerformanceCardProps> = ({ data, openPositions = [], onDrillDown }) => {
  const isEmpty = data.totalTrades === 0;
  const hasOpen = openPositions.length > 0;
  const periodPnl = data.periodPnl ?? 0;
  const winRate = data.totalTrades > 0
    ? (data.winningTrades / data.totalTrades) * 100
    : 0;
  const broker = data.brokerProvider ? (BROKER_LABEL[data.brokerProvider] ?? data.brokerProvider) : null;
  // Distinct instruments actually traded — pulled from the breakdown
  const instruments = data.instrumentBreakdown
    .filter((row) => row.instrument && row.instrument !== 'UNKNOWN')
    .map((row) => row.instrument);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onDrillDown(data.accountId)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onDrillDown(data.accountId);
        }
      }}
      style={{
        background: 'var(--bg-surface)',
        border: '1px solid var(--border-primary)',
        borderRadius: 'var(--radius-md)',
        padding: 16,
        cursor: 'pointer',
        transition: 'border-color var(--transition-fast)',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLElement).style.borderColor = 'var(--text-secondary)';
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-primary)';
      }}
    >
      {/* Header: label + meta + sparkline */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
            <span style={{
              width: 8, height: 8, borderRadius: '50%',
              background: data.autopilotEnabled === true
                ? 'var(--success)'
                : data.autopilotEnabled === false
                  ? 'var(--text-muted)'
                  : 'var(--warning)',
              flexShrink: 0,
            }} title={
              data.autopilotEnabled === true ? 'Autopilot ON' :
              data.autopilotEnabled === false ? 'Autopilot OFF' : 'Autopilot unknown'
            } />
            <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {data.accountLabel}
            </span>
          </div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 10, color: 'var(--text-muted)' }}>
            {broker && <span style={{ textTransform: 'uppercase', letterSpacing: 0.5 }}>{broker}</span>}
            {data.accountKind && <span>· {data.accountKind}</span>}
            {data.openPositionsCount != null && data.openPositionsCount > 0 && (
              <span style={{ color: 'var(--warning)' }}>· {data.openPositionsCount} open</span>
            )}
          </div>
        </div>
        <Sparkline points={data.equitySparkline} />
      </div>

      {/* Balance + period P&L */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}>
        <div>
          <div style={{ fontSize: 9, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5 }}>Equity</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 600 }}>
            {formatCurrency(data.equity)}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 9, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5 }}>Period P&amp;L</div>
          <div style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 14,
            fontWeight: 600,
            color: pnlColor(periodPnl) ?? 'var(--text-primary)',
          }}>
            {periodPnl >= 0 ? '+' : ''}{formatCurrency(periodPnl)}
          </div>
          <div style={{ fontSize: 10, color: pnlColor(data.periodPercentChange) ?? 'var(--text-secondary)' }}>
            {formatPercent(data.periodPercentChange)}
          </div>
        </div>
      </div>

      {/* Running trades — currently-open positions on this account.
       *  Shown above the period stats so the operator sees live activity
       *  alongside the historical summary. */}
      {hasOpen && (
        <div style={{
          borderTop: '1px solid var(--border-primary)',
          paddingTop: 8,
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
        }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            fontSize: 9,
            color: 'var(--text-muted)',
            textTransform: 'uppercase',
            letterSpacing: 0.5,
            marginBottom: 2,
          }}>
            <span style={{
              width: 6, height: 6, borderRadius: '50%',
              background: 'var(--warning)',
              boxShadow: '0 0 6px var(--warning)',
            }} />
            Running · {openPositions.length}
          </div>
          {openPositions.map((p) => {
            const pnl = p.unrealized_pnl ?? 0;
            return (
              <div
                key={p.id}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr auto auto',
                  gap: 8,
                  alignItems: 'baseline',
                  fontSize: 11,
                  fontFamily: 'var(--font-mono)',
                }}
              >
                <span style={{ color: 'var(--text-primary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  <span style={{ color: p.direction === 'BUY' ? 'var(--success)' : 'var(--danger)', fontWeight: 600 }}>
                    {p.direction}
                  </span>{' '}
                  {p.instrument}
                </span>
                <span style={{ color: 'var(--text-secondary)', fontSize: 10 }}>
                  {p.position_size.toFixed(2)} @ {p.entry_price.toFixed(2)}
                </span>
                <span style={{ color: pnlColor(pnl) ?? 'var(--text-primary)', fontWeight: 600 }}>
                  {pnl >= 0 ? '+' : ''}{formatCurrency(pnl)}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {isEmpty ? (
        !hasOpen && (
          <div style={{
            padding: '12px 0', textAlign: 'center',
            fontSize: 12, color: 'var(--text-muted)',
            borderTop: '1px solid var(--border-primary)',
          }}>
            No closed trades in this period
          </div>
        )
      ) : (
        <>
          {/* Trade quality strip */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, 1fr)',
            gap: 4,
            fontSize: 11,
            borderTop: '1px solid var(--border-primary)',
            paddingTop: 8,
          }}>
            <div>
              <div style={{ color: 'var(--text-muted)', fontSize: 9 }}>Trades</div>
              <div style={{ fontFamily: 'var(--font-mono)' }}>{data.totalTrades}</div>
            </div>
            <div>
              <div style={{ color: 'var(--text-muted)', fontSize: 9 }}>Win rate</div>
              <div style={{
                fontFamily: 'var(--font-mono)',
                color: winRate >= 50 ? 'var(--success)' : winRate > 0 ? 'var(--warning)' : 'var(--text-primary)',
              }}>
                {winRate.toFixed(0)}%
              </div>
            </div>
            <div>
              <div style={{ color: 'var(--text-muted)', fontSize: 9 }}>Wins</div>
              <div style={{ fontFamily: 'var(--font-mono)', color: 'var(--success)' }}>{data.winningTrades}</div>
            </div>
            <div>
              <div style={{ color: 'var(--text-muted)', fontSize: 9 }}>Losses</div>
              <div style={{ fontFamily: 'var(--font-mono)', color: 'var(--danger)' }}>{data.losingTrades}</div>
            </div>
          </div>

          {/* Instruments traded */}
          {instruments.length > 0 && (
            <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              <span>Instruments: </span>
              {instruments.map((sym, i) => (
                <span key={sym} style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
                  {sym}{i < instruments.length - 1 ? ', ' : ''}
                </span>
              ))}
            </div>
          )}

          {/* Last activity */}
          {data.lastTradeAt && (
            <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
              Last trade: {timeAgo(data.lastTradeAt)}
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default AccountPerformanceCard;
