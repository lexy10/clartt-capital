import { type FC, useEffect, useState, useCallback } from 'react';
import { useAccountStore } from '../stores/accountStore';
import { apiClient } from '../services/ApiClient';
import AccountCard from './AccountCard';
import AddAccountForm from './AddAccountForm';

const AccountsPage: FC = () => {
  const { accounts, loading, error, fetchAccounts } = useAccountStore();
  const [showAddForm, setShowAddForm] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    fetchAccounts();
  }, [fetchAccounts]);

  // Reset dismissed state when a new error comes in
  useEffect(() => {
    if (error) setDismissed(false);
  }, [error]);

  const activeAccounts = accounts.filter((a) => a.isActive);

  const [refreshing, setRefreshing] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const handleOverlayClick = useCallback(() => {
    if (!deploying) setShowAddForm(false);
  }, [deploying]);

  const handleCloseForm = useCallback(async () => {
    setShowAddForm(false);
    // Silent refresh — don't show loading spinner
    try {
      const accounts = await apiClient.accounts.list();
      useAccountStore.setState({ accounts });
    } catch {
      // Fall back to normal fetch if silent refresh fails
      await fetchAccounts();
    }
    // Bump key so AccountCards re-mount and re-fetch their strategies
    setRefreshKey((k) => k + 1);
  }, [fetchAccounts]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await fetchAccounts();
    setRefreshing(false);
  }, [fetchAccounts]);

  return (
    <div style={{ padding: '16px', fontFamily: 'var(--font-sans)', color: 'var(--text-primary)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
        <h2 style={{ margin: 0, fontSize: '16px' }}>Accounts</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            style={refreshBtnStyle}
            title="Refresh accounts"
            aria-label="Refresh accounts"
          >
            <span style={{
              display: 'inline-block',
              transition: 'transform 0.3s ease',
              ...(refreshing ? { animation: 'spin 0.8s linear infinite' } : {}),
            }}>↻</span>
          </button>
          <button
            onClick={() => setShowAddForm(true)}
            style={addBtnStyle}
          >
            + Add Account
          </button>
        </div>
      </div>

      {error && !dismissed && (
        <div style={errorBannerStyle} role="alert">
          <span>{error}</span>
          <button
            onClick={() => setDismissed(true)}
            style={dismissBtnStyle}
            aria-label="Dismiss error"
          >
            ✕
          </button>
        </div>
      )}

      {showAddForm && (
        <div style={modalOverlayStyle} onClick={handleOverlayClick}>
          <div style={modalContentStyle} onClick={(e) => e.stopPropagation()}>
            <AddAccountForm
              onClose={handleCloseForm}
              onDeployingChange={setDeploying}
            />
          </div>
        </div>
      )}

      {loading && (
        <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Loading accounts…</p>
      )}

      {!loading && activeAccounts.length === 0 && (
        <p style={{ fontSize: '13px', color: 'var(--text-muted)', textAlign: 'center', marginTop: '40px' }}>
          No accounts connected. Add your first Deriv or MT5 account.
        </p>
      )}

      {activeAccounts.length > 0 && (
        <div style={gridStyle}>
          {activeAccounts.map((account) => (
            <AccountCard key={`${account.id}-${refreshKey}`} account={account} />
          ))}
        </div>
      )}

      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
};

const addBtnStyle: React.CSSProperties = {
  background: 'var(--accent)',
  color: '#fff',
  border: 'none',
  borderRadius: 'var(--radius-sm)',
  padding: '6px 14px',
  fontSize: '12px',
  fontWeight: 600,
  cursor: 'pointer',
};

const refreshBtnStyle: React.CSSProperties = {
  background: 'var(--bg-surface)',
  color: 'var(--text-secondary)',
  border: '1px solid var(--glass-border)',
  borderRadius: 'var(--radius-sm)',
  padding: '6px 10px',
  fontSize: '14px',
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  transition: 'all 0.15s ease',
};

const errorBannerStyle: React.CSSProperties = {
  background: 'var(--danger-bg, rgba(239,68,68,0.1))',
  border: '1px solid var(--danger, #ef4444)',
  borderRadius: 'var(--radius-md)',
  padding: '8px 12px',
  marginBottom: '12px',
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  fontSize: '12px',
  color: 'var(--danger, #ef4444)',
};

const dismissBtnStyle: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: 'var(--danger, #ef4444)',
  fontSize: '14px',
  cursor: 'pointer',
  padding: '0 4px',
  lineHeight: 1,
};

const gridStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
  gap: '12px',
};

const modalOverlayStyle: React.CSSProperties = {
  position: 'fixed',
  top: 0,
  left: 0,
  right: 0,
  bottom: 0,
  background: 'rgba(0, 0, 0, 0.6)',
  backdropFilter: 'blur(4px)',
  WebkitBackdropFilter: 'blur(4px)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 1000,
};

const modalContentStyle: React.CSSProperties = {
  width: '100%',
  maxWidth: 480,
  maxHeight: '85vh',
  overflowY: 'auto',
};

export default AccountsPage;
