import { type FC, useState } from 'react';
import { usePerformanceStore } from '../../stores/performanceStore';
import type { StrategyPerformanceData, StrategyTradeData } from '../../types/api';

function pnlColor(value: number): string {
  if (value > 0) return 'var(--success)';
  if (value < 0) return 'var(--danger)';
  return 'var(--text-secondary)';
}

const thStyle: React.CSSProperties = {
  textAlign: 'left',
  fontWeight: 500,
  padding: '8px 12px',
  borderBottom: '1px solid var(--bg-surface)',
  color: 'var(--text-secondary)',
  fontSize: 12,
  fontFamily: 'var(--font-sans)',
};

const tdStyle: React.CSSProperties = {
  padding: '8px 12px',
  fontSize: 12,
  fontFamily: 'var(--font-mono)',
  color: 'var(--text-primary)',
  borderBottom: '1px solid var(--bg-surface)',
};

const TradeRow: FC<{ trade: StrategyTradeData }> = ({ trade }) => (
  <tr>
    <td style={{ ...tdStyle, fontFamily: 'var(--font-sans)' }}>{trade.instrument}</td>
    <td style={tdStyle}>{trade.direction}</td>
    <td style={{ ...tdStyle, color: pnlColor(trade.profitLoss) }}>{trade.profitLoss.toFixed(2)}</td>
    <td style={{ ...tdStyle, color: pnlColor(trade.actualR) }}>{trade.actualR.toFixed(2)}</td>
    <td style={tdStyle}>{trade.plannedRR.toFixed(2)}</td>
    <td style={{ ...tdStyle, color: pnlColor(trade.actualRR) }}>{trade.actualRR.toFixed(2)}</td>
    <td style={{ ...tdStyle, fontFamily: 'var(--font-sans)', fontSize: 11, color: 'var(--text-secondary)' }}>
      {trade.exitTime ? new Date(trade.exitTime).toLocaleDateString() : '—'}
    </td>
  </tr>
);

const StrategyRow: FC<{ strategy: StrategyPerformanceData }> = ({ strategy }) => {
  const [expanded, setExpanded] = useState(false);

  return (
    <>
      <tr
        onClick={() => setExpanded(!expanded)}
        style={{ cursor: 'pointer' }}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpanded(!expanded); } }}
      >
        <td style={{ ...tdStyle, fontFamily: 'var(--font-sans)', fontWeight: 500 }}>
          <span style={{ marginRight: 6, fontSize: 10, color: 'var(--text-secondary)' }}>{expanded ? '▼' : '▶'}</span>
          {strategy.strategyName}
        </td>
        <td style={{ ...tdStyle, color: pnlColor(strategy.cumulativeR) }}>{strategy.cumulativeR.toFixed(2)}</td>
        <td style={{ ...tdStyle, color: pnlColor(strategy.avgR) }}>{strategy.avgR.toFixed(2)}</td>
        <td style={tdStyle}>{strategy.winRate.toFixed(1)}%</td>
        <td style={tdStyle}>{strategy.totalTrades}</td>
        <td style={tdStyle}>{strategy.avgPlannedRR.toFixed(2)}</td>
        <td style={tdStyle}>{strategy.avgActualRR.toFixed(2)}</td>
      </tr>
      {expanded && strategy.trades.length > 0 && (
        <tr>
          <td colSpan={7} style={{ padding: 0 }}>
            <div style={{ background: 'var(--bg-primary)', padding: '8px 16px 8px 32px' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={thStyle}>Instrument</th>
                    <th style={thStyle}>Dir</th>
                    <th style={thStyle}>P&L</th>
                    <th style={thStyle}>Actual R</th>
                    <th style={thStyle}>Planned RR</th>
                    <th style={thStyle}>Actual RR</th>
                    <th style={thStyle}>Date</th>
                  </tr>
                </thead>
                <tbody>
                  {strategy.trades.map((trade) => (
                    <TradeRow key={trade.tradeId} trade={trade} />
                  ))}
                </tbody>
              </table>
            </div>
          </td>
        </tr>
      )}
      {expanded && strategy.trades.length === 0 && (
        <tr>
          <td colSpan={7} style={{ ...tdStyle, fontFamily: 'var(--font-sans)', color: 'var(--text-secondary)', textAlign: 'center' }}>
            No trades in this period
          </td>
        </tr>
      )}
    </>
  );
};

const StrategyComparisonView: FC = () => {
  const strategies = usePerformanceStore((s) => s.strategies);
  const loading = usePerformanceStore((s) => s.strategiesLoading);
  const error = usePerformanceStore((s) => s.strategiesError);
  const fetchStrategyPerformance = usePerformanceStore((s) => s.fetchStrategyPerformance);

  if (loading) {
    return (
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--text-secondary)', padding: 16 }}>
        Loading strategies…
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: 16, fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--danger)', display: 'flex', alignItems: 'center', gap: 12 }}>
        <span>{error}</span>
        <button
          onClick={() => { fetchStrategyPerformance(); }}
          style={{
            background: 'none',
            border: '1px solid var(--danger)',
            borderRadius: 'var(--radius-sm)',
            color: 'var(--danger)',
            fontFamily: 'var(--font-sans)',
            fontSize: 12,
            padding: '4px 10px',
            cursor: 'pointer',
          }}
        >
          Retry
        </button>
      </div>
    );
  }

  if (strategies.length === 0) {
    return (
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--text-secondary)', padding: 16, textAlign: 'center' }}>
        No strategies found
      </div>
    );
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={thStyle}>Strategy</th>
            <th style={thStyle}>Cumulative R</th>
            <th style={thStyle}>Avg R</th>
            <th style={thStyle}>Win Rate</th>
            <th style={thStyle}>Trades</th>
            <th style={thStyle}>Avg Planned RR</th>
            <th style={thStyle}>Avg Actual RR</th>
          </tr>
        </thead>
        <tbody>
          {strategies.map((s) => (
            <StrategyRow key={s.strategyId} strategy={s} />
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default StrategyComparisonView;
