import { type FC, useEffect } from 'react';
import { usePortfolioStore } from '../stores/portfolioStore';
import { usePerformanceStore } from '../stores/performanceStore';

const fmt = (v: number) =>
  `$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const pnlColor = (v: number) =>
  v > 0 ? 'var(--success)' : v < 0 ? 'var(--danger)' : 'var(--text-primary)';

const PositionsPage: FC = () => {
  const { positions, loading, fetchPositions } = usePortfolioStore();
  const accounts = usePerformanceStore((s) => s.accounts);
  const fetchAccounts = usePerformanceStore((s) => s.fetchAccounts);

  useEffect(() => {
    fetchPositions();
    fetchAccounts();
  }, [fetchPositions, fetchAccounts]);

  // Build account label map
  const accountLabels = new Map(accounts.map((a) => [a.accountId, a.accountLabel]));

  const totalUnrealized = positions.reduce((sum, p) => sum + (p.unrealized_pnl ?? 0), 0);

  return (
    <div style={{ padding: 24, fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-primary)', minHeight: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>Open Positions</h1>
        <div style={{ display: 'flex', gap: 24, alignItems: 'center' }}>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            Total: <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)', fontWeight: 600 }}>{positions.length}</span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            Unrealized P&L:{' '}
            <span style={{ fontFamily: 'var(--font-mono)', color: pnlColor(totalUnrealized), fontWeight: 600 }}>
              {fmt(totalUnrealized)}
            </span>
          </div>
        </div>
      </div>

      {loading && positions.length === 0 && (
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', padding: 16 }}>Loading positions…</div>
      )}

      {!loading && positions.length === 0 && (
        <div style={{
          textAlign: 'center', padding: 48, color: 'var(--text-secondary)', fontSize: 14,
          background: 'var(--bg-surface)', borderRadius: 'var(--radius-sm)',
        }}>
          No open positions across any account
        </div>
      )}

      {positions.length > 0 && (
        <div style={{ background: 'var(--bg-surface)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr>
                  {['Account', 'Instrument', 'Direction', 'Entry Price', 'Current Price', 'Size', 'Unrealized P&L', 'Opened'].map((h) => (
                    <th
                      key={h}
                      style={{
                        textAlign: h === 'Account' || h === 'Instrument' || h === 'Direction' || h === 'Opened' ? 'left' : 'right',
                        padding: '10px 12px',
                        fontFamily: 'var(--font-sans)',
                        fontWeight: 500,
                        color: 'var(--text-secondary)',
                        borderBottom: '1px solid var(--text-secondary)',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {positions.map((pos) => {
                  const pnl = pos.unrealized_pnl ?? 0;
                  const accountName = accountLabels.get(pos.account_id) || pos.account_id.slice(0, 8);
                  const opened = new Date(pos.opened_at);
                  const openedStr = opened.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
                    ' ' + opened.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });

                  return (
                    <tr key={pos.id} style={{ borderBottom: '1px solid var(--bg-primary)' }}>
                      <td style={{ padding: '8px 12px', fontFamily: 'var(--font-sans)', color: 'var(--text-primary)' }}>{accountName}</td>
                      <td style={{ padding: '8px 12px', fontFamily: 'var(--font-sans)', color: 'var(--text-primary)' }}>{pos.instrument}</td>
                      <td style={{
                        padding: '8px 12px', fontFamily: 'var(--font-sans)',
                        color: pos.direction === 'BUY' ? 'var(--success)' : 'var(--danger)',
                      }}>
                        {pos.direction}
                      </td>
                      <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', textAlign: 'right', color: 'var(--text-primary)' }}>
                        {pos.entry_price.toFixed(2)}
                      </td>
                      <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', textAlign: 'right', color: 'var(--text-primary)' }}>
                        {pos.current_price?.toFixed(2) ?? '—'}
                      </td>
                      <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', textAlign: 'right', color: 'var(--text-primary)' }}>
                        {pos.position_size}
                      </td>
                      <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', textAlign: 'right', color: pnlColor(pnl) }}>
                        {fmt(pnl)}
                      </td>
                      <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                        {openedStr}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};

export default PositionsPage;
