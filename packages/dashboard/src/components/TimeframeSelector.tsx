import { type FC } from 'react';
import type { Timeframe } from '../types/timeframe';
import { useChartStore } from '../stores/chartStore';

const TIMEFRAMES: { value: Timeframe; label: string }[] = [
  { value: '1m', label: '1m' },
  { value: '5m', label: '5m' },
  { value: '15m', label: '15m' },
  { value: '30m', label: '30m' },
  { value: '1h', label: '1h' },
  { value: '4h', label: '4h' },
  { value: '1d', label: '1D' },
];

interface TimeframeSelectorProps {
  active: Timeframe;
  onChange: (timeframe: Timeframe) => void;
}

const TimeframeSelector: FC<TimeframeSelectorProps> = ({ active, onChange }) => {
  const fitContent = useChartStore((s) => s.fitContent);

  return (
    <div style={{ display: 'flex', gap: 2, alignItems: 'center' }}>
      {TIMEFRAMES.map(({ value, label }) => (
        <button
          key={value}
          className={`tf-btn mono${value === active ? ' tf-btn-active' : ''}`}
          aria-label={`${label} timeframe`}
          aria-pressed={value === active}
          onClick={() => onChange(value)}
        >
          {label}
        </button>
      ))}
      <div style={{ width: 1, height: 14, background: '#30363d', margin: '0 4px' }} />
      <button
        className="tf-btn mono"
        aria-label="Auto-fit chart to visible data"
        title="Auto-fit"
        onClick={fitContent}
      >
        A
      </button>
    </div>
  );
};

export default TimeframeSelector;
