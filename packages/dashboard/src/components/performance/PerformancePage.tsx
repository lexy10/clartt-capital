import { type FC, useEffect } from 'react';
import { usePerformanceStore } from '../../stores/performanceStore';
import TimePeriodSelector from './TimePeriodSelector';
import AggregateOverview from './AggregateOverview';
import AccountPerformanceCard from './AccountPerformanceCard';
import TradeDrillDown from './TradeDrillDown';
import StrategyComparisonView from './StrategyComparisonView';

const ErrorSection: FC<{ message: string; onRetry: () => void }> = ({ message, onRetry }) => (
  <div
    style={{
      padding: 16,
      fontFamily: 'var(--font-sans)',
      fontSize: 13,
      color: 'var(--danger)',
      display: 'flex',
      alignItems: 'center',
      gap: 12,
    }}
  >
    <span>{message}</span>
    <button
      onClick={onRetry}
      style={{
        background: 'none',
        border: '1px solid var(--danger)',
        borderRadius: 'var(--radius-sm)',
        color: 'var(--danger)',
        fontFamily: 'var(--font-sans)',
        fontSize: 12,
        padding: '4px 10px',
        cursor: 'pointer',
      }}
    >
      Retry
    </button>
  </div>
);

const TabButton: FC<{ label: string; active: boolean; onClick: () => void }> = ({ label, active, onClick }) => (
  <button
    onClick={onClick}
    style={{
      background: 'none',
      border: 'none',
      borderBottom: active ? '2px solid var(--text-primary)' : '2px solid transparent',
      color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
      fontFamily: 'var(--font-sans)',
      fontSize: 14,
      fontWeight: active ? 600 : 400,
      padding: '8px 16px',
      cursor: 'pointer',
      transition: 'all var(--transition-fast)',
    }}
  >
    {label}
  </button>
);

const PerformancePage: FC = () => {
  const fetchOverview = usePerformanceStore((s) => s.fetchOverview);
  const fetchAccounts = usePerformanceStore((s) => s.fetchAccounts);
  const fetchStrategyPerformance = usePerformanceStore((s) => s.fetchStrategyPerformance);
  const fetchDrillDown = usePerformanceStore((s) => s.fetchDrillDown);
  const closeDrillDown = usePerformanceStore((s) => s.closeDrillDown);
  const activeTab = usePerformanceStore((s) => s.activeTab);
  const setActiveTab = usePerformanceStore((s) => s.setActiveTab);

  const accounts = usePerformanceStore((s) => s.accounts);
  const drillDown = usePerformanceStore((s) => s.drillDown);

  const accountsLoading = usePerformanceStore((s) => s.accountsLoading);
  const accountsError = usePerformanceStore((s) => s.accountsError);
  const overviewError = usePerformanceStore((s) => s.overviewError);
  const drillDownLoading = usePerformanceStore((s) => s.drillDownLoading);
  const drillDownError = usePerformanceStore((s) => s.drillDownError);

  useEffect(() => {
    fetchOverview();
    fetchAccounts();
  }, [fetchOverview, fetchAccounts]);

  const handleTabSwitch = (tab: 'accounts' | 'strategies') => {
    setActiveTab(tab);
    if (tab === 'strategies') {
      fetchStrategyPerformance();
    }
  };

  return (
    <div
      style={{
        padding: 24,
        fontFamily: 'var(--font-sans)',
        color: 'var(--text-primary)',
        background: 'var(--bg-primary)',
        minHeight: '100%',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h1 style={{ fontFamily: 'var(--font-sans)', fontSize: 20, fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
          Performance
        </h1>
        <TimePeriodSelector />
      </div>

      {/* Aggregate Overview */}
      {overviewError ? (
        <ErrorSection message={overviewError} onRetry={fetchOverview} />
      ) : (
        <AggregateOverview />
      )}

      {/* Tab Bar */}
      <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid var(--bg-surface)', marginTop: 24, marginBottom: 16 }}>
        <TabButton label="Accounts" active={activeTab === 'accounts'} onClick={() => handleTabSwitch('accounts')} />
        <TabButton label="Strategies" active={activeTab === 'strategies'} onClick={() => handleTabSwitch('strategies')} />
      </div>

      {/* Tab Content */}
      {activeTab === 'accounts' ? (
        <div>
          {drillDown ? (
            <>
              {drillDownError ? (
                <ErrorSection message={drillDownError} onRetry={() => fetchDrillDown(drillDown.accountId)} />
              ) : (
                <>
                  {drillDownLoading && (
                    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8 }}>
                      Loading trades…
                    </div>
                  )}
                  <TradeDrillDown trades={drillDown.trades} onBack={closeDrillDown} />
                </>
              )}
            </>
          ) : (
            <>
              {accountsError ? (
                <ErrorSection message={accountsError} onRetry={fetchAccounts} />
              ) : (
                <>
                  {accountsLoading && accounts.length === 0 && (
                    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--text-secondary)', padding: 16 }}>
                      Loading accounts…
                    </div>
                  )}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: 16 }}>
                    {accounts.map((account) => (
                      <AccountPerformanceCard key={account.accountId} data={account} onDrillDown={fetchDrillDown} />
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      ) : (
        <StrategyComparisonView />
      )}
    </div>
  );
};

export default PerformancePage;
