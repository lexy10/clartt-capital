import { type FC, useEffect, useState } from 'react';
import { useReconciliationStore } from '../stores/reconciliationStore';

const ReconciliationNotificationBanner: FC = () => {
  const recentAlert = useReconciliationStore((s) => s.recentAlert);
  const dismissAlert = useReconciliationStore((s) => s.dismissAlert);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (recentAlert) {
      setVisible(true);
      const timer = setTimeout(() => {
        setVisible(false);
        dismissAlert();
      }, 15000);
      return () => clearTimeout(timer);
    }
  }, [recentAlert, dismissAlert]);

  if (!visible || !recentAlert) return null;

  const criticalCount = recentAlert.discrepancies.filter((d) => d.severity === 'critical').length;
  const warningCount = recentAlert.discrepancies.filter((d) => d.severity === 'warning').length;

  return (
    <div
      role="alert"
      style={{
        padding: '12px 16px',
        backgroundColor: criticalCount > 0 ? '#fef2f2' : '#fffbeb',
        borderLeft: `4px solid ${criticalCount > 0 ? '#ef4444' : '#f59e0b'}`,
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        fontSize: '14px',
      }}
    >
      <span>
        Reconciliation alert for account {recentAlert.accountId.slice(0, 8)}…:
        {criticalCount > 0 && ` ${criticalCount} critical`}
        {warningCount > 0 && ` ${warningCount} warning`}
        {` discrepanc${recentAlert.discrepancies.length === 1 ? 'y' : 'ies'}`}
      </span>
      <button
        onClick={() => { setVisible(false); dismissAlert(); }}
        style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '16px' }}
        aria-label="Dismiss alert"
      >
        ✕
      </button>
    </div>
  );
};

export default ReconciliationNotificationBanner;
