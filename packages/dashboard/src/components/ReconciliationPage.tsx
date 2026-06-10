import { type FC, useEffect } from 'react';
import { useReconciliationStore } from '../stores/reconciliationStore';
import ReconciliationHistoryView from './ReconciliationHistoryView';
import ReconciliationConfigPanel from './ReconciliationConfigPanel';
import ReconciliationNotificationBanner from './ReconciliationNotificationBanner';

const ReconciliationPage: FC = () => {
  const subscribeToAlerts = useReconciliationStore((s) => s.subscribeToAlerts);
  const unsubscribeFromAlerts = useReconciliationStore((s) => s.unsubscribeFromAlerts);

  useEffect(() => {
    subscribeToAlerts();
    return () => unsubscribeFromAlerts();
  }, [subscribeToAlerts, unsubscribeFromAlerts]);

  return (
    <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '24px' }}>
      <ReconciliationNotificationBanner />
      <ReconciliationHistoryView />
      <ReconciliationConfigPanel />
    </div>
  );
};

export default ReconciliationPage;
