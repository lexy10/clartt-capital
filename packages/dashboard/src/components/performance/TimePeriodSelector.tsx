import { type FC } from 'react';
import { usePerformanceStore, type TimePeriod } from '../../stores/performanceStore';

const PERIODS: { value: TimePeriod; label: string }[] = [
  { value: 'today', label: 'Today' },
  { value: 'this_week', label: 'This Week' },
  { value: 'this_month', label: 'This Month' },
  { value: 'all_time', label: 'All Time' },
];

const TimePeriodSelector: FC = () => {
  const period = usePerformanceStore((s) => s.period);
  const setPeriodAndFetch = usePerformanceStore((s) => s.setPeriodAndFetch);

  return (
    <div style={{ display: 'flex', gap: 4 }}>
      {PERIODS.map(({ value, label }) => (
        <button
          key={value}
          onClick={() => setPeriodAndFetch(value)}
          aria-pressed={value === period}
          style={{
            padding: '6px 14px',
            fontSize: 12,
            fontFamily: 'var(--font-sans)',
            fontWeight: value === period ? 600 : 400,
            color: value === period ? 'var(--text-primary)' : 'var(--text-secondary)',
            background: value === period ? 'var(--bg-surface)' : 'transparent',
            border: value === period ? '1px solid var(--text-secondary)' : '1px solid transparent',
            borderRadius: 'var(--radius-sm)',
            cursor: 'pointer',
            transition: 'all var(--transition-fast)',
          }}
        >
          {label}
        </button>
      ))}
    </div>
  );
};

export default TimePeriodSelector;
