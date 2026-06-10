import { type FC, useEffect, useState } from 'react';
import { useReconciliationStore } from '../stores/reconciliationStore';

const ReconciliationHistoryView: FC = () => {
  const { reports, totalReports, currentPage, totalPages, loading, fetchReports } =
    useReconciliationStore();
  const [statusFilter, setStatusFilter] = useState('');
  const [accountFilter, setAccountFilter] = useState('');

  useEffect(() => {
    fetchReports({ status: statusFilter || undefined, account_id: accountFilter || undefined });
  }, [fetchReports, statusFilter, accountFilter]);

  return (
    <div>
      <h3 style={{ margin: '0 0 12px' }}>Reconciliation History</h3>
      <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          aria-label="Filter by status"
          style={{ padding: '6px 8px', borderRadius: '4px', border: '1px solid #374151' }}
        >
          <option value="">All statuses</option>
          <option value="clean">Clean</option>
          <option value="discrepancies_found">Discrepancies</option>
          <option value="error">Error</option>
          <option value="broker_unreachable">Broker Unreachable</option>
        </select>
        <input
          type="text"
          placeholder="Account ID"
          value={accountFilter}
          onChange={(e) => setAccountFilter(e.target.value)}
          aria-label="Filter by account ID"
          style={{ padding: '6px 8px', borderRadius: '4px', border: '1px solid #374151', width: '200px' }}
        />
      </div>

      {loading ? (
        <p>Loading…</p>
      ) : (
        <>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #374151', textAlign: 'left' }}>
                <th style={{ padding: '8px' }}>Time</th>
                <th style={{ padding: '8px' }}>Account</th>
                <th style={{ padding: '8px' }}>Status</th>
                <th style={{ padding: '8px' }}>Discrepancies</th>
                <th style={{ padding: '8px' }}>Duration</th>
              </tr>
            </thead>
            <tbody>
              {reports.map((r) => (
                <tr key={r.id} style={{ borderBottom: '1px solid #1f2937' }}>
                  <td style={{ padding: '8px' }}>{new Date(r.cycleTimestamp).toLocaleString()}</td>
                  <td style={{ padding: '8px' }}>{r.accountId.slice(0, 8)}…</td>
                  <td style={{ padding: '8px' }}>{r.status}</td>
                  <td style={{ padding: '8px' }}>{r.discrepancies.length}</td>
                  <td style={{ padding: '8px' }}>{r.durationMs}ms</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ marginTop: '8px', fontSize: '13px' }}>
            Page {currentPage} of {totalPages} ({totalReports} total)
            {currentPage > 1 && (
              <button onClick={() => fetchReports({ page: currentPage - 1, status: statusFilter || undefined })} style={{ marginLeft: '8px' }}>
                Prev
              </button>
            )}
            {currentPage < totalPages && (
              <button onClick={() => fetchReports({ page: currentPage + 1, status: statusFilter || undefined })} style={{ marginLeft: '4px' }}>
                Next
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
};

export default ReconciliationHistoryView;
