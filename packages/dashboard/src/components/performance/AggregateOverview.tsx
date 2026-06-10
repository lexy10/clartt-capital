import { type FC } from 'react';
import { usePerformanceStore } from '../../stores/performanceStore';
import type { AggregateOverviewData } from '../../types/api';

function formatPercent(value: number): string {
  return `${value.toFixed(2)}%`;
}

function formatRatio(value: number): string {
  return value.toFixed(2);
}

function formatMultiplier(value: number): string {
  return `${value.toFixed(2)}x`;
}

function pnlColor(value: number): string | undefined {
  if (value > 0) return 'var(--success)';
  if (value < 0) return 'var(--danger)';
  return undefined;
}

interface MetricCardProps {
  label: string;
  value: string;
  valueColor?: string;
}

const MetricCard: FC<MetricCardProps> = ({ label, value, valueColor }) => (
  <div
    style={{
      background: 'var(--bg-surface)',
      borderRadius: 'var(--radius-sm)',
      padding: '16px 20px',
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
      minWidth: 0,
    }}
  >
    <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--text-secondary)', fontWeight: 500 }}>
      {label}
    </span>
    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 20, fontWeight: 600, color: valueColor ?? 'var(--text-primary)' }}>
      {value}
    </span>
  </div>
);

function buildCards(data: AggregateOverviewData): MetricCardProps[] {
  const noTrades = data.totalTrades === 0;
  const changeSign = data.periodPercentChange >= 0 ? '+' : '';

  return [
    {
      label: 'Return Multiplier',
      value: formatMultiplier(data.returnMultiplier),
      valueColor: pnlColor(data.returnMultiplier - 1),
    },
    {
      label: 'Period % Change',
      value: `${changeSign}${formatPercent(data.periodPercentChange)}`,
      valueColor: pnlColor(data.periodPercentChange),
    },
    {
      label: 'Win Rate',
      value: formatPercent(data.winRate),
    },
    {
      label: 'Profit Factor',
      value: formatRatio(data.profitFactor),
    },
    {
      label: 'Avg R:R',
      value: formatRatio(data.avgRiskReward),
    },
    {
      label: 'Max Drawdown',
      value: noTrades && data.maxDrawdown === 0 ? '—' : formatPercent(data.maxDrawdown),
    },
  ];
}

const EMPTY_DATA: AggregateOverviewData = {
  returnMultiplier: 1.0,
  periodPercentChange: 0,
  totalBalance: 0,
  totalEquity: 0,
  todayPnl: 0,
  winRate: 0,
  profitFactor: 0,
  avgRiskReward: 0,
  maxDrawdown: 0,
  totalTrades: 0,
};

const AggregateOverview: FC = () => {
  const overview = usePerformanceStore((s) => s.overview);
  const loading = usePerformanceStore((s) => s.overviewLoading);
  const error = usePerformanceStore((s) => s.overviewError);

  const data = overview ?? EMPTY_DATA;
  const cards = buildCards(data);

  if (error) {
    return (
      <div style={{ padding: 16, color: 'var(--danger)', fontFamily: 'var(--font-sans)', fontSize: 13 }}>
        {error}
      </div>
    );
  }

  return (
    <div style={{ position: 'relative' }}>
      {loading && (
        <div style={{ position: 'absolute', top: 0, right: 0, padding: '4px 10px', fontSize: 11, fontFamily: 'var(--font-sans)', color: 'var(--text-secondary)' }}>
          Updating…
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
        {cards.map((card) => (
          <MetricCard key={card.label} {...card} />
        ))}
      </div>
    </div>
  );
};

export default AggregateOverview;
