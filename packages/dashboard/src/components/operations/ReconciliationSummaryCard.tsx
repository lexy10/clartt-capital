import { type FC } from 'react';
import { useReconciliationStore } from '../../stores/reconciliationStore';
import { ROUTES } from '../../types/api';
import { StatusBadge, DetailLink, timeAgo, type Tone } from './OpsHelpers';

/** Right-rail compact card showing the latest reconciliation cycle status,
 *  open critical/warning counts, and a link to the full reports page. */
const ReconciliationSummaryCard: FC = () => {
  const reports = useReconciliationStore((s) => s.reports);
  const recentAlert = useReconciliationStore((s) => s.recentAlert);
  const latest = reports[0];
  const discrepancies = recentAlert?.discrepancies ?? latest?.discrepancies ?? [];
  const criticalCount = discrepancies.filter((item) => item.severity === 'critical').length;
  const warningCount = discrepancies.filter((item) => item.severity === 'warning').length;
  const hasError = latest?.status === 'error';
  const tone: Tone = hasError || criticalCount > 0
    ? 'danger'
    : warningCount > 0 || discrepancies.length > 0
      ? 'warning'
      : 'success';

  return (
    <div className="live-desk-card live-desk-card-compact">
      <div className="live-desk-card-header">
        <span>Reconciliation</span>
        <StatusBadge tone={tone} label={tone === 'success' ? 'Clean' : 'Review'} />
      </div>
      <div className="live-desk-pair-row">
        <span>Latest status</span>
        <strong>{latest?.status?.replace(/_/g, ' ') ?? 'No report'}</strong>
      </div>
      <div className="live-desk-pair-row">
        <span>Critical</span>
        <strong className={criticalCount > 0 ? 'live-desk-text-danger' : ''}>
          {criticalCount}
        </strong>
      </div>
      <div className="live-desk-pair-row">
        <span>Warnings</span>
        <strong className={warningCount > 0 ? 'live-desk-text-warning' : ''}>
          {warningCount}
        </strong>
      </div>
      <div className="live-desk-card-footer">
        <span>{latest ? timeAgo(latest.cycleTimestamp) : 'No cycle'}</span>
        <DetailLink to={ROUTES.RECONCILIATION}>Reports</DetailLink>
      </div>
    </div>
  );
};

export default ReconciliationSummaryCard;
