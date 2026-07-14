import { type FC, useEffect, useState, useRef } from 'react';
import type { TradingAccount } from '../types/trading-account';
import type { Strategy, AccountStrategy } from '../types/api';
import { useAccountStore } from '../stores/accountStore';
import { useStrategyStore } from '../stores/strategyStore';
import { apiClient } from '../services/ApiClient';

interface AccountCardProps {
  account: TradingAccount;
}

const fmt = (v: number) =>
  v.toLocaleString('en-US', { style: 'currency', currency: 'USD' });

type ConnectionStatus = 'loading' | 'connected' | 'disconnected';

const AccountCard: FC<AccountCardProps> = ({ account }) => {
  const {
    accountDetails, fetchDetails, fetchStatus, updateLabel, removeAccount,
    deployAccount, undeployAccount,
  } = useAccountStore();
  const { strategies, fetchStrategies } = useStrategyStore();

  const [editing, setEditing] = useState(false);
  const [labelDraft, setLabelDraft] = useState(account.label || account.mt5Login || account.derivLoginId || 'Account');
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('loading');
  const [confirmingRemove, setConfirmingRemove] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const [accountStrategies, setAccountStrategies] = useState<AccountStrategy[]>([]);
  const [editingStrategies, setEditingStrategies] = useState(false);
  const [selectedStrategyIds, setSelectedStrategyIds] = useState<Set<string>>(new Set());
  const [savingStrategies, setSavingStrategies] = useState(false);
  const [strategyError, setStrategyError] = useState<string | null>(null);

  const [autopilotEnabled, setAutopilotEnabled] = useState(false);
  const [autopilotLoading, setAutopilotLoading] = useState(false);

  const details = accountDetails[account.id];

  useEffect(() => {
    if (!details) return;
    if (details.state === 'DEPLOYED' && details.connection_status === 'CONNECTED') {
      setConnectionStatus('connected');
    } else if (details.state === 'DEPLOYING') {
      setConnectionStatus('loading');
    } else {
      setConnectionStatus('disconnected');
    }
  }, [details]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setConnectionStatus('loading');
      await fetchDetails(account.id);
      if (cancelled) return;
      const d = useAccountStore.getState().accountDetails[account.id];
      if (!d) { setConnectionStatus('disconnected'); return; }
      if (d.state === 'DEPLOYED' && d.connection_status === 'CONNECTED') {
        setConnectionStatus('connected');
      } else if (d.state === 'DEPLOYING') {
        setConnectionStatus('loading');
      } else {
        setConnectionStatus('disconnected');
      }
    };
    load();
    return () => { cancelled = true; };
  }, [account.id, fetchDetails]);

  useEffect(() => {
    apiClient.accounts.getStrategies(account.id)
      .then(setAccountStrategies)
      .catch(() => {});
  }, [account.id]);

  useEffect(() => {
    apiClient.autopilot.getState(account.id)
      .then((state) => setAutopilotEnabled(state.enabled))
      .catch(() => {});
  }, [account.id]);

  const handleToggleAutopilot = async () => {
    if (autopilotLoading) return;
    const prev = autopilotEnabled;
    const next = !autopilotEnabled;
    setAutopilotEnabled(next);
    setAutopilotLoading(true);
    try {
      await apiClient.autopilot.setState(account.id, next);
    } catch {
      setAutopilotEnabled(prev);
    } finally {
      setAutopilotLoading(false);
    }
  };

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const displayLabel = account.label || account.mt5Login || account.derivLoginId || 'Account';
  const isDeriv = account.brokerProvider === 'deriv';
  const brokerBadge = isDeriv ? 'Deriv' : account.brokerProvider === 'metaapi' || account.mt5Login ? 'MT5' : (account.brokerProvider || '').toUpperCase();
  const accountIdentifier = isDeriv ? account.derivLoginId : account.mt5Login;

  const commitLabel = () => {
    setEditing(false);
    const trimmed = labelDraft.trim();
    if (trimmed && trimmed !== displayLabel) {
      updateLabel(account.id, trimmed);
    } else {
      setLabelDraft(displayLabel);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') commitLabel();
    if (e.key === 'Escape') { setLabelDraft(displayLabel); setEditing(false); }
  };

  const handleDeploy = async () => {
    setActionLoading('deploy');
    await deployAccount(account.id);
    setConnectionStatus('loading');
    setActionLoading(null);
    const status = await fetchStatus(account.id);
    if (status?.state === 'DEPLOYED' && status?.connection_status === 'CONNECTED') {
      await fetchDetails(account.id);
      const d = useAccountStore.getState().accountDetails[account.id];
      setConnectionStatus(d && d.connection_status !== 'TIMEOUT' ? 'connected' : 'disconnected');
    }
  };

  const handleUndeploy = async () => {
    setActionLoading('undeploy');
    await undeployAccount(account.id);
    setConnectionStatus('disconnected');
    setActionLoading(null);
  };

  const handleRemove = async () => {
    if (confirmingRemove) {
      setActionLoading('remove');
      await removeAccount(account.id);
      setConfirmingRemove(false);
      setActionLoading(null);
    } else {
      setConfirmingRemove(true);
    }
  };

  const getStrategyInstruments = (strategy: Strategy): string[] => {
    const instruments = strategy.config?.instruments;
    if (Array.isArray(instruments)) return instruments as string[];
    return [];
  };

  const handleEditStrategies = async () => {
    await fetchStrategies();
    const ids = new Set(accountStrategies.map((as) => as.strategyId));
    setSelectedStrategyIds(ids);
    setStrategyError(null);
    setEditingStrategies(true);
  };

  const handleToggleStrategy = (id: string) => {
    setSelectedStrategyIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSaveStrategies = async () => {
    setSavingStrategies(true);
    setStrategyError(null);
    try {
      const updated = await apiClient.accounts.setStrategies(
        account.id,
        Array.from(selectedStrategyIds),
      );
      setAccountStrategies(updated);
      setEditingStrategies(false);
    } catch (err) {
      setStrategyError(err instanceof Error ? err.message : 'Failed to update strategies');
    } finally { setSavingStrategies(false); }
  };

  const statusConfig = {
    loading: { label: 'Loading', className: 'badge badge-muted', dot: false, pulse: true },
    connected: { label: 'Connected', className: 'badge badge-success', dot: true, pulse: false },
    disconnected: { label: 'Disconnected', className: 'badge badge-danger', dot: false, pulse: false },
  }[connectionStatus];

  return (
    <div style={cardStyle}>
      <div style={glowEdgeStyle} />

      {/* Header: label + status */}
      <div style={headerStyle}>
        <div style={headerLeftStyle}>
          {editing ? (
            <input
              ref={inputRef}
              type="text"
              value={labelDraft}
              onChange={(e) => setLabelDraft(e.target.value)}
              onBlur={commitLabel}
              onKeyDown={handleKeyDown}
              style={labelInputStyle}
              aria-label="Account label"
            />
          ) : (
            <span
              onClick={() => setEditing(true)}
              style={labelStyle}
              title="Click to edit label"
              role="button"
              tabIndex={0}
              onKeyDown={(e) => { if (e.key === 'Enter') setEditing(true); }}
            >
              {displayLabel}
            </span>
          )}
          {brokerBadge && (
            <span style={{
              ...loginTagStyle,
              background: isDeriv ? 'var(--accent-dim)' : 'var(--bg-surface)',
              color: isDeriv ? 'var(--accent)' : 'var(--text-muted)',
              borderColor: isDeriv ? 'var(--border-glow)' : 'var(--glass-border)',
            }}>{brokerBadge}</span>
          )}
          {accountIdentifier && (
            <span style={loginTagStyle}>{accountIdentifier}</span>
          )}
        </div>
        <span className={statusConfig.className} style={statusConfig.pulse ? { opacity: 0.7 } : undefined}>
          {statusConfig.dot && <span style={dotStyle} />}
          {statusConfig.label}
        </span>
      </div>

      {/* Server info (MT5 only) */}
      {account.mt5Server && (
        <div style={serverRowStyle}>
          <span style={serverLabelStyle}>Server</span>
          <span style={serverValueStyle}>{account.mt5Server}</span>
        </div>
      )}

      {/* Stats or loading/disconnected state */}
      {connectionStatus === 'loading' ? (
        <div style={placeholderStyle}>
          <div style={shimmerRow} />
          <div style={{ ...shimmerRow, width: '60%' }} />
          <div style={{ ...shimmerRow, width: '80%' }} />
        </div>
      ) : details && connectionStatus === 'connected' ? (
        <div style={statsGridStyle}>
          <div style={statStyle}>
            <span style={statLabelStyle}>Balance</span>
            <span style={statValueStyle}>{fmt(details.balance)}</span>
          </div>
          <div style={statStyle}>
            <span style={statLabelStyle}>Equity</span>
            <span style={statValueStyle}>{fmt(details.equity)}</span>
          </div>
          <div style={statStyle}>
            <span style={statLabelStyle}>Free Margin</span>
            <span style={statValueStyle}>{fmt(details.free_margin)}</span>
          </div>
          <div style={statStyle}>
            <span style={statLabelStyle}>Positions</span>
            <span style={statValueStyle}>{details.open_positions}</span>
          </div>
          <div style={statStyle}>
            <span style={statLabelStyle}>Margin</span>
            <span style={statValueStyle}>{fmt(details.margin)}</span>
          </div>
          <div style={statStyle}>
            <span style={statLabelStyle}>Leverage</span>
            <span style={statValueStyle}>1:{details.leverage}</span>
          </div>
        </div>
      ) : (
        <p style={disconnectedMsgStyle}>
          {(() => {
            const s = useAccountStore.getState().accountStatuses[account.id];
            if (s?.state === 'DEPLOYING') return 'Account deploying — waiting for broker…';
            if (s?.state === 'UNDEPLOYED') return 'Account not deployed';
            if (s?.state === 'DEPLOYED' && s?.connection_status !== 'CONNECTED')
              return 'Account deployed — connecting to broker…';
            if (details?.connection_status === 'TIMEOUT')
              return 'Connection timed out — retrying…';
            return 'Account not connected';
          })()}
        </p>
      )}

      {/* Auto Trading toggle */}
      <div style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={sectionLabelStyle}>Auto Trading</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            {autopilotEnabled && (
              <span style={{
                fontSize: 9, fontWeight: 700, color: 'var(--success)',
                textTransform: 'uppercase', letterSpacing: '1px',
              }}>ON</span>
            )}
            <button
              type="button"
              role="switch"
              aria-checked={autopilotEnabled}
              aria-label={`Auto trading ${autopilotEnabled ? 'enabled' : 'disabled'}`}
              disabled={autopilotLoading}
              onClick={handleToggleAutopilot}
              style={{
                position: 'relative', width: 34, height: 18, borderRadius: 9,
                background: autopilotEnabled ? 'var(--accent)' : 'var(--bg-surface)',
                border: `1px solid ${autopilotEnabled ? 'var(--accent)' : 'var(--border-primary)'}`,
                cursor: autopilotLoading ? 'not-allowed' : 'pointer',
                transition: 'all 250ms cubic-bezier(0.4, 0, 0.2, 1)',
                boxShadow: autopilotEnabled ? '0 0 10px rgba(129, 140, 248, 0.3)' : 'none',
                opacity: autopilotLoading ? 0.4 : 1, flexShrink: 0,
              }}
            >
              <span style={{
                position: 'absolute', top: 2, left: autopilotEnabled ? 18 : 2,
                width: 12, height: 12, borderRadius: '50%',
                background: autopilotEnabled ? '#fff' : 'var(--text-muted)',
                transition: 'all 250ms cubic-bezier(0.4, 0, 0.2, 1)',
                boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
              }} />
            </button>
          </div>
        </div>
      </div>

      {/* Strategies */}
      <div style={sectionStyle}>
        <div style={sectionHeaderStyle}>
          <span style={sectionLabelStyle}>Strategies</span>
          <button onClick={handleEditStrategies} style={smallBtnStyle}>Edit</button>
        </div>
        {accountStrategies.length > 0 ? (
          <div style={badgesRowStyle}>
            {accountStrategies.map((as) => {
              const instruments = getStrategyInstruments(as.strategy);
              return (
                <span key={as.id} style={strategyBadgeStyle} title={instruments.length > 0 ? instruments.join(', ') : undefined}>
                  {as.strategy.name}
                </span>
              );
            })}
          </div>
        ) : (
          <span style={mutedTextStyle}>No strategies assigned</span>
        )}
      </div>

      {/* Edit strategies panel */}
      {editingStrategies && (
        <div style={editPanelStyle}>
          {strategyError && <div style={inlineErrorStyle}>{strategyError}</div>}
          {strategies.length === 0 && (
            <span style={mutedTextStyle}>No strategies available. Create one in the Strategies page.</span>
          )}
          {strategies.filter((s) => s.enabled !== false).map((s) => {
            const checked = selectedStrategyIds.has(s.id);
            const instruments = getStrategyInstruments(s);
            return (
              <div key={s.id} style={{ marginBottom: 6 }}>
                <label style={checkLabelStyle}>
                  <input type="checkbox" checked={checked} onChange={() => handleToggleStrategy(s.id)} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--text-primary)' }}>{s.name}</span>
                      <span style={{ fontSize: 9, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>{s.algorithm}</span>
                    </div>
                    {instruments.length > 0 && (
                      <div style={{ marginTop: 2, display: 'flex', gap: 3, flexWrap: 'wrap' }}>
                        {instruments.map((inst) => (
                          <span key={inst} style={{
                            fontSize: 8, padding: '0px 5px', borderRadius: 3,
                            background: 'var(--bg-surface)', color: 'var(--text-muted)',
                            border: '1px solid var(--glass-border)', fontFamily: 'var(--font-mono)',
                          }}>{inst}</span>
                        ))}
                      </div>
                    )}
                  </div>
                </label>
              </div>
            );
          })}
          <div style={editActionsStyle}>
            <button onClick={handleSaveStrategies} disabled={savingStrategies} style={accentBtnStyle}>
              {savingStrategies ? 'Saving…' : 'Save'}
            </button>
            <button onClick={() => { setEditingStrategies(false); setStrategyError(null); }} disabled={savingStrategies} style={smallBtnStyle}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Actions footer */}
      <div style={footerStyle}>
        {confirmingRemove ? (
          <div style={confirmRowStyle}>
            <span style={{ fontSize: 11, color: 'var(--danger)' }}>Remove this account?</span>
            <button onClick={handleRemove} disabled={actionLoading === 'remove'} style={dangerBtnStyle}>
              {actionLoading === 'remove' ? 'Removing…' : 'Confirm'}
            </button>
            {actionLoading !== 'remove' && (
              <button onClick={() => setConfirmingRemove(false)} style={smallBtnStyle}>Cancel</button>
            )}
          </div>
        ) : (
          <div style={actionsRowStyle}>
            <div style={{ display: 'flex', gap: 6 }}>
              {connectionStatus === 'disconnected' && (
                <button onClick={handleDeploy} disabled={!!actionLoading} style={accentBtnStyle}>
                  {actionLoading === 'deploy' ? 'Deploying…' : 'Deploy'}
                </button>
              )}
              {connectionStatus === 'connected' && (
                <button onClick={handleUndeploy} disabled={!!actionLoading} style={warningBtnStyle}>
                  {actionLoading === 'undeploy' ? 'Undeploying…' : 'Undeploy'}
                </button>
              )}
            </div>
            <button onClick={handleRemove} disabled={!!actionLoading} style={dangerOutlineBtnStyle}>Remove</button>
          </div>
        )}
      </div>
    </div>
  );
};

// ── Styles ──────────────────────────────────────────────────

const cardStyle: React.CSSProperties = {
  position: 'relative',
  background: 'var(--glass-bg)',
  backdropFilter: 'blur(var(--glass-blur))',
  WebkitBackdropFilter: 'blur(var(--glass-blur))',
  border: '1px solid var(--glass-border)',
  borderRadius: 'var(--radius-lg)',
  padding: '16px',
  transition: 'all 150ms ease',
  overflow: 'hidden',
};

const glowEdgeStyle: React.CSSProperties = {
  position: 'absolute',
  top: 0, left: 0, right: 0,
  height: 1,
  background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.06), transparent)',
};

const headerStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'flex-start',
  marginBottom: 10,
  gap: 8,
};

const headerLeftStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 2,
  minWidth: 0,
  flex: 1,
};

const labelStyle: React.CSSProperties = {
  fontSize: 13,
  fontWeight: 600,
  color: 'var(--text-primary)',
  cursor: 'pointer',
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
};

const labelInputStyle: React.CSSProperties = {
  background: 'transparent',
  border: '1px solid var(--glass-border)',
  borderRadius: 'var(--radius-sm)',
  color: 'var(--text-primary)',
  fontSize: 13,
  fontWeight: 600,
  padding: '2px 6px',
  outline: 'none',
  width: '100%',
};

const loginTagStyle: React.CSSProperties = {
  fontSize: 10,
  fontFamily: 'var(--font-mono)',
  color: 'var(--text-muted)',
  letterSpacing: '0.3px',
  padding: '1px 6px',
  borderRadius: 4,
  border: '1px solid var(--glass-border)',
  background: 'var(--bg-surface)',
  whiteSpace: 'nowrap',
};

const dotStyle: React.CSSProperties = {
  width: 5,
  height: 5,
  borderRadius: '50%',
  background: 'var(--success)',
  boxShadow: '0 0 6px var(--success)',
  display: 'inline-block',
};

const serverRowStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  padding: '6px 0',
  marginBottom: 8,
  borderBottom: '1px solid var(--glass-border)',
};

const serverLabelStyle: React.CSSProperties = {
  fontSize: 9,
  fontWeight: 600,
  color: 'var(--text-muted)',
  textTransform: 'uppercase',
  letterSpacing: '0.8px',
};

const serverValueStyle: React.CSSProperties = {
  fontSize: 11,
  fontFamily: 'var(--font-mono)',
  color: 'var(--text-secondary)',
};

const placeholderStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 8,
  padding: '12px 0',
};

const shimmerRow: React.CSSProperties = {
  height: 12,
  borderRadius: 4,
  background: 'linear-gradient(90deg, var(--bg-surface) 25%, var(--bg-hover) 50%, var(--bg-surface) 75%)',
  backgroundSize: '200% 100%',
  animation: 'shimmer 1.5s infinite',
};

const statsGridStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: '1fr 1fr 1fr',
  gap: 10,
  padding: '8px 0',
};

const statStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 2,
};

const statLabelStyle: React.CSSProperties = {
  fontSize: 9,
  fontWeight: 600,
  color: 'var(--text-muted)',
  textTransform: 'uppercase',
  letterSpacing: '0.6px',
};

const statValueStyle: React.CSSProperties = {
  fontSize: 13,
  fontWeight: 600,
  color: 'var(--text-primary)',
  fontFamily: 'var(--font-mono)',
  letterSpacing: '-0.3px',
};

const disconnectedMsgStyle: React.CSSProperties = {
  fontSize: 12,
  color: 'var(--text-muted)',
  padding: '16px 0',
  textAlign: 'center',
};

const sectionStyle: React.CSSProperties = {
  marginTop: 8,
  paddingTop: 8,
  borderTop: '1px solid var(--glass-border)',
};

const sectionHeaderStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  marginBottom: 6,
};

const sectionLabelStyle: React.CSSProperties = {
  fontSize: 9,
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.5px',
  color: 'var(--text-muted)',
};

const badgesRowStyle: React.CSSProperties = {
  display: 'flex',
  flexWrap: 'wrap',
  gap: 4,
};

const strategyBadgeStyle: React.CSSProperties = {
  display: 'inline-block',
  background: 'var(--bg-surface)',
  border: '1px solid var(--glass-border)',
  borderRadius: 12,
  padding: '2px 8px',
  fontSize: 10,
  fontWeight: 500,
  color: 'var(--text-primary)',
  whiteSpace: 'nowrap',
};

const mutedTextStyle: React.CSSProperties = {
  fontSize: 11,
  color: 'var(--text-muted)',
  fontStyle: 'italic',
};

const editPanelStyle: React.CSSProperties = {
  marginTop: 8,
  padding: 8,
  background: 'var(--bg-surface)',
  border: '1px solid var(--glass-border)',
  borderRadius: 'var(--radius-sm)',
};

const inlineErrorStyle: React.CSSProperties = {
  fontSize: 11,
  color: 'var(--danger)',
  marginBottom: 6,
  padding: '4px 6px',
  background: 'var(--danger-bg)',
  borderRadius: 'var(--radius-sm)',
};

const checkLabelStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'flex-start',
  gap: 6,
  fontSize: 11,
  color: 'var(--text-primary)',
  cursor: 'pointer',
};

const editActionsStyle: React.CSSProperties = {
  display: 'flex',
  gap: 6,
  justifyContent: 'flex-end',
  marginTop: 8,
};

const footerStyle: React.CSSProperties = {
  marginTop: 10,
  paddingTop: 10,
  borderTop: '1px solid var(--glass-border)',
};

const actionsRowStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
};

const confirmRowStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  justifyContent: 'flex-end',
};

const btnBase: React.CSSProperties = {
  border: 'none',
  borderRadius: 'var(--radius-sm)',
  fontSize: 11,
  fontWeight: 600,
  padding: '5px 12px',
  cursor: 'pointer',
  transition: 'all 150ms ease',
};

const smallBtnStyle: React.CSSProperties = {
  ...btnBase,
  background: 'none',
  border: '1px solid var(--glass-border)',
  color: 'var(--text-muted)',
  padding: '3px 8px',
  fontSize: 10,
};

const accentBtnStyle: React.CSSProperties = {
  ...btnBase,
  background: 'var(--accent)',
  color: '#fff',
};

const warningBtnStyle: React.CSSProperties = {
  ...btnBase,
  background: 'var(--warning-bg)',
  border: '1px solid var(--warning)',
  color: 'var(--warning)',
};

const dangerBtnStyle: React.CSSProperties = {
  ...btnBase,
  background: 'var(--danger)',
  color: '#fff',
};

const dangerOutlineBtnStyle: React.CSSProperties = {
  ...btnBase,
  background: 'none',
  border: '1px solid var(--danger-border)',
  color: 'var(--danger)',
  padding: '4px 10px',
};

export default AccountCard;
