import { type FC } from 'react';
import { useAutopilotStore } from '../stores/autopilotStore';

function formatPnL(value: number): string {
  const sign = value >= 0 ? '+' : '';
  return `${sign}$${Math.abs(value).toFixed(2)}`;
}

const AutopilotStatusCard: FC = () => {
  const enabled = useAutopilotStore((s) => s.enabled);
  const strategyName = useAutopilotStore((s) => s.strategyName);
  const openPositionCount = useAutopilotStore((s) => s.openPositionCount);
  const sessionPnL = useAutopilotStore((s) => s.sessionPnL);

  return (
    <div className="card" role="region" aria-label="Autopilot status">
      <div className="card-header">
        <span className="card-title">Autopilot</span>
        <span className={`badge ${enabled ? 'badge-success' : 'badge-muted'}`}>
          {enabled ? 'Active' : 'Inactive'}
        </span>
      </div>

      <div className="stats-grid">
        <div className="stat-item">
          <span className="stat-label">Strategy</span>
          <span className="stat-value" style={{ fontSize: 13, fontFamily: 'var(--font-sans)' }}>
            {strategyName || '—'}
          </span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Positions</span>
          <span className="stat-value">{openPositionCount}</span>
        </div>
        <div className="stat-item" style={{ gridColumn: '1 / -1' }}>
          <span className="stat-label">Session P/L</span>
          <span className={`stat-value ${sessionPnL > 0 ? 'positive' : sessionPnL < 0 ? 'negative' : ''}`}
            style={{ fontSize: 20 }}>
            {formatPnL(sessionPnL)}
          </span>
        </div>
      </div>
    </div>
  );
};

export default AutopilotStatusCard;
