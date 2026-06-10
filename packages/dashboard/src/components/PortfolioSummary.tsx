import { type FC, useEffect } from 'react';
import { usePortfolioStore } from '../stores/portfolioStore';
import { wsManager } from '../services/WebSocketManager';
import type { TradeExecutionResult } from '../types/trade';

const fmt = (v: number) => v.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
const pnlClass = (v: number) => v > 0 ? 'positive' : v < 0 ? 'negative' : '';

const PortfolioSummary: FC = () => {
  const { balance, equity, unrealizedPnl, positions, loading, error, fetchSummary, fetchPositions } = usePortfolioStore();

  useEffect(() => { fetchSummary(); fetchPositions(); }, [fetchSummary, fetchPositions]);

  useEffect(() => {
    const subId = wsManager.subscribe('trades', (_trade: TradeExecutionResult) => {
      fetchSummary();
      fetchPositions();
    });
    return () => { wsManager.unsubscribe(subId); };
  }, [fetchSummary, fetchPositions]);

  const marginUsed = equity > 0 ? equity - balance : 0;

  return (
    <div>
      <div className="card" style={{ marginBottom: 12 }}>
        <div className="card-header">
          <span className="card-title">Portfolio</span>
          {error && <span className="badge badge-danger">Error</span>}
        </div>

        <div className="stats-grid">
          <div className="stat-item">
            <span className="stat-label">Balance</span>
            <span className="stat-value">{fmt(balance)}</span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Equity</span>
            <span className="stat-value">{fmt(equity)}</span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Unrealized P/L</span>
            <span className={`stat-value ${pnlClass(unrealizedPnl)}`}>{fmt(unrealizedPnl)}</span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Margin</span>
            <span className="stat-value">{fmt(marginUsed)}</span>
          </div>
        </div>
      </div>

      {/* Positions */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">
            Positions {positions.length > 0 && <span style={{ color: 'var(--text-muted)' }}>({positions.length})</span>}
          </span>
        </div>

        {loading && <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>Loading…</p>}

        {!loading && positions.length === 0 && (
          <div className="empty-state">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <circle cx="12" cy="12" r="10" />
              <path d="M8 12h8" />
            </svg>
            <span>No open positions</span>
          </div>
        )}

        {positions.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table className="positions-table">
              <thead>
                <tr>
                  <th>Pair</th>
                  <th>Side</th>
                  <th>Entry</th>
                  <th>Current</th>
                  <th>Size</th>
                  <th>P/L</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((pos) => {
                  const pnl = pos.unrealized_pnl ?? 0;
                  return (
                    <tr key={pos.id}>
                      <td>{pos.instrument}</td>
                      <td style={{ color: pos.direction === 'BUY' ? 'var(--success)' : 'var(--danger)' }}>
                        {pos.direction}
                      </td>
                      <td>{pos.entry_price.toFixed(2)}</td>
                      <td>{pos.current_price?.toFixed(2) ?? '—'}</td>
                      <td>{pos.position_size}</td>
                      <td style={{ color: pnl > 0 ? 'var(--success)' : pnl < 0 ? 'var(--danger)' : 'var(--text-secondary)' }}>
                        {fmt(pnl)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

export default PortfolioSummary;
