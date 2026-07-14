import { type FC, type FormEvent, useState, useEffect } from 'react';
import type { CreateAccountDto } from '../types/trading-account';
import type { Strategy, AccountInstrument } from '../types/api';
import { useAccountStore } from '../stores/accountStore';
import { useStrategyStore } from '../stores/strategyStore';
import { apiClient } from '../services/ApiClient';

interface AddAccountFormProps {
  onClose: () => void;
  onDeployingChange?: (deploying: boolean) => void;
}

type Step = 'credentials' | 'deploying' | 'instruments' | 'strategies';

const AddAccountForm: FC<AddAccountFormProps> = ({ onClose, onDeployingChange }) => {
  const { addAccount } = useAccountStore();
  const { strategies, fetchStrategies, loading: strategiesLoading } = useStrategyStore();

  const [step, setStep] = useState<Step>('credentials');
  // Which broker flow — Deriv-direct (synthetics) or MetaAPI/MT5.
  const [brokerType, setBrokerType] = useState<'deriv' | 'metaapi'>('deriv');
  const [accountKind, setAccountKind] = useState<'personal' | 'demo' | 'prop'>('personal');
  // MetaAPI / MT5 fields
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [serverName, setServerName] = useState('');
  const [platform, setPlatform] = useState<'mt5' | 'mt4'>('mt5');
  // Deriv fields
  const [derivApiToken, setDerivApiToken] = useState('');
  const [derivLoginId, setDerivLoginId] = useState('');
  const [label, setLabel] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  const [deployStatus, setDeployStatus] = useState('');

  // Instrument mapping state
  const [accountInstruments, setAccountInstruments] = useState<AccountInstrument[]>([]);
  const [brokerSymbolOptions, setBrokerSymbolOptions] = useState<string[]>([]);
  const [brokerSymbolMap, setBrokerSymbolMap] = useState<Record<string, string>>({});
  const [loadingInstruments] = useState(false);
  const [savingInstruments, setSavingInstruments] = useState(false);

  // Strategy state
  const [selectedStrategies, setSelectedStrategies] = useState<Set<string>>(new Set());
  const [createdAccountId, setCreatedAccountId] = useState<string | null>(null);
  const [savingStrategies, setSavingStrategies] = useState(false);

  useEffect(() => { fetchStrategies(); }, [fetchStrategies]);

  const handleSubmitCredentials = async (e: FormEvent) => {
    e.preventDefault();
    setFormError(null);

    // Validate + build the DTO per broker flow.
    let dto: CreateAccountDto;
    if (brokerType === 'deriv') {
      if (!derivApiToken.trim() || !derivLoginId.trim()) {
        setFormError('Deriv API token and login ID are required.');
        return;
      }
      dto = {
        brokerProvider: 'deriv',
        derivApiToken: derivApiToken.trim(),
        derivLoginId: derivLoginId.trim(),
        accountKind,
        ...(label.trim() ? { label: label.trim() } : {}),
      };
    } else {
      if (!login.trim() || !password || !serverName.trim()) {
        setFormError('Login, password, and server name are required.');
        return;
      }
      dto = {
        brokerProvider: 'metaapi',
        login: login.trim(), password, serverName: serverName.trim(), platform,
        accountKind,
        ...(label.trim() ? { label: label.trim() } : {}),
      };
    }

    setStep('deploying');
    onDeployingChange?.(true);
    setDeployStatus('Sending credentials to server…');
    await new Promise((r) => setTimeout(r, 600));
    setDeployStatus(brokerType === 'deriv' ? 'Connecting to Deriv…' : 'Creating MetaAPI account…');
    await addAccount(dto);
    const storeError = useAccountStore.getState().error;
    if (storeError) { setFormError(storeError); setStep('credentials'); onDeployingChange?.(false); return; }
    setDeployStatus(brokerType === 'deriv' ? 'Authorizing Deriv token…' : 'Deploying account to broker…');
    const accounts = useAccountStore.getState().accounts;
    const created = brokerType === 'deriv'
      ? accounts.find((a) => a.derivLoginId === derivLoginId.trim())
      : accounts.find((a) => a.mt5Login === login.trim() && a.mt5Server === serverName.trim());
    if (!created) { setFormError('Account created but could not be found.'); setStep('credentials'); onDeployingChange?.(false); return; }
    setCreatedAccountId(created.id);
    setDeployStatus('Waiting for broker connection…');
    await new Promise((r) => setTimeout(r, 800));
    setDeployStatus('Loading instruments…');

    // Load auto-associated instruments and broker symbols
    try {
      const [instruments, symbols] = await Promise.all([
        apiClient.accounts.getInstruments(created.id),
        apiClient.accounts.getBrokerSymbols(created.id).catch(() => [] as string[]),
      ]);
      setAccountInstruments(instruments);
      setBrokerSymbolOptions(symbols);
      // Initialize broker symbol map with current values
      const initialMap: Record<string, string> = {};
      for (const ai of instruments) {
        initialMap[ai.instrumentId] = ai.brokerSymbol || ai.instrument?.symbol || '';
      }
      setBrokerSymbolMap(initialMap);
    } catch {
      // Non-fatal — user can still set them manually
    }

    setStep('instruments');
    onDeployingChange?.(false);
  };

  const handleSaveInstruments = async () => {
    if (!createdAccountId) return;
    setSavingInstruments(true);
    setFormError(null);
    try {
      const items = accountInstruments.map((ai) => ({
        instrumentId: ai.instrumentId,
        brokerSymbol: brokerSymbolMap[ai.instrumentId] || ai.instrument?.symbol || '',
      }));
      await apiClient.accounts.setInstruments(createdAccountId, items);
      setStep('strategies');
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to save instrument mappings');
    }
    setSavingInstruments(false);
  };

  const handleToggleStrategy = (strategyId: string) => {
    setSelectedStrategies((prev) => {
      const next = new Set(prev);
      if (next.has(strategyId)) next.delete(strategyId);
      else next.add(strategyId);
      return next;
    });
  };

  const handleComplete = async () => {
    if (createdAccountId && selectedStrategies.size > 0) {
      setSavingStrategies(true);
      setFormError(null);
      try {
        await apiClient.accounts.setStrategies(createdAccountId, Array.from(selectedStrategies));
      } catch (err) {
        setFormError(err instanceof Error ? err.message : 'Failed to save strategy assignments');
        setSavingStrategies(false);
        return;
      }
      setSavingStrategies(false);
    }
    onClose();
  };

  const getStrategyInstruments = (strategy: Strategy): string[] => {
    const instruments = strategy.config?.instruments;
    if (Array.isArray(instruments)) return instruments as string[];
    return [];
  };

  const stepNames = ['Credentials', 'Deploy', 'Instruments', 'Strategies'];
  const stepIndex = step === 'credentials' ? 0 : step === 'deploying' ? 1 : step === 'instruments' ? 2 : 3;

  const renderStepIndicator = () => (
    <div style={stepIndicatorStyle}>
      {stepNames.map((name, i) => (
        <div key={name} style={{ display: 'contents' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0 }}>
            <div style={{
              width: 22, height: 22, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 10, fontWeight: 700, flexShrink: 0,
              background: i <= stepIndex ? 'var(--accent)' : 'var(--bg-surface)',
              color: i <= stepIndex ? '#fff' : 'var(--text-muted)',
              border: i <= stepIndex ? 'none' : '1px solid var(--glass-border)',
              transition: 'all 0.3s ease',
              boxShadow: i === stepIndex ? '0 0 12px var(--accent-glow)' : 'none',
            }}>
              {i < stepIndex ? '✓' : i + 1}
            </div>
            <span style={{
              fontSize: 10, fontWeight: 600, letterSpacing: '0.3px', whiteSpace: 'nowrap',
              color: i <= stepIndex ? 'var(--text-primary)' : 'var(--text-muted)',
              transition: 'color 0.3s ease',
            }}>{name}</span>
          </div>
          {i < stepNames.length - 1 && <div style={{
            flex: '1 1 12px', minWidth: 8, height: 1,
            background: i < stepIndex ? 'var(--accent)' : 'var(--glass-border)',
            transition: 'background 0.3s ease',
          }} />}
        </div>
      ))}
    </div>
  );

  // ── Step 1: Credentials ──────────────────────────────────
  if (step === 'credentials') {
    return (
      <div style={modalCardStyle}>
        <div style={headerStyle}>
          <div>
            <h3 style={titleStyle}>Add Trading Account</h3>
            <p style={subtitleStyle}>
              {brokerType === 'deriv'
                ? 'Connect a Deriv account (synthetic indices)'
                : 'Connect a MetaTrader (MT5/MT4) account'}
            </p>
          </div>
          <button onClick={onClose} style={closeBtnStyle} aria-label="Close">✕</button>
        </div>
        {renderStepIndicator()}
        <form onSubmit={handleSubmitCredentials}>
          {formError && <div style={errorStyle} role="alert">{formError}</div>}

          {/* Broker selector */}
          <div style={{ marginBottom: 14 }}>
            <label style={labelStyle}>Broker</label>
            <div style={brokerToggleStyle}>
              {([
                { key: 'deriv', label: 'Deriv', sub: 'Synthetics (R_25, R_75…)' },
                { key: 'metaapi', label: 'MetaTrader', sub: 'MT5 / MT4 via MetaAPI' },
              ] as const).map((opt) => {
                const active = brokerType === opt.key;
                return (
                  <button
                    key={opt.key}
                    type="button"
                    onClick={() => { setBrokerType(opt.key); setFormError(null); }}
                    style={{
                      ...brokerOptStyle,
                      background: active ? 'var(--accent-dim)' : 'var(--bg-surface)',
                      borderColor: active ? 'var(--border-glow)' : 'var(--glass-border)',
                      boxShadow: active ? '0 0 10px var(--accent-glow)' : 'none',
                    }}
                  >
                    <span style={{ fontSize: 12, fontWeight: 600, color: active ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
                      {opt.label}
                    </span>
                    <span style={{ fontSize: 9, color: 'var(--text-muted)', marginTop: 2 }}>{opt.sub}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {brokerType === 'deriv' ? (
            <>
              <div style={{ ...fieldStyle, marginBottom: 12 }}>
                <label htmlFor="deriv-token" style={labelStyle}>API Token</label>
                <input id="deriv-token" type="password" value={derivApiToken}
                  onChange={(e) => setDerivApiToken(e.target.value)}
                  style={inputStyle} placeholder="Deriv API token" autoComplete="off" required />
                <div style={hintStyle}>
                  Create one at app.deriv.com → Settings → API token (needs “Trade” + “Read” scopes).
                </div>
              </div>
              <div style={fieldsGridStyle}>
                <div style={fieldStyle}>
                  <label htmlFor="deriv-loginid" style={labelStyle}>Login ID</label>
                  <input id="deriv-loginid" type="text" value={derivLoginId}
                    onChange={(e) => setDerivLoginId(e.target.value)}
                    style={inputStyle} placeholder="CR1234567 / VRTC1234567" required />
                </div>
                <div style={fieldStyle}>
                  <label htmlFor="deriv-kind" style={labelStyle}>Account Type</label>
                  <select id="deriv-kind" value={accountKind}
                    onChange={(e) => setAccountKind(e.target.value as 'personal' | 'demo' | 'prop')}
                    style={inputStyle}>
                    <option value="personal">Real</option>
                    <option value="demo">Demo</option>
                    <option value="prop">Prop firm</option>
                  </select>
                </div>
              </div>
            </>
          ) : (
            <div style={fieldsGridStyle}>
              <div style={fieldStyle}>
                <label htmlFor="mt5-login" style={labelStyle}>Login</label>
                <input id="mt5-login" type="text" value={login} onChange={(e) => setLogin(e.target.value)}
                  style={inputStyle} placeholder="12345678" required />
              </div>
              <div style={fieldStyle}>
                <label htmlFor="mt5-password" style={labelStyle}>Password</label>
                <input id="mt5-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                  style={inputStyle} placeholder="••••••••" autoComplete="off" required />
              </div>
              <div style={fieldStyle}>
                <label htmlFor="mt5-server" style={labelStyle}>Server</label>
                <input id="mt5-server" type="text" value={serverName} onChange={(e) => setServerName(e.target.value)}
                  style={inputStyle} placeholder="ICMarketsSC-Demo" required />
              </div>
              <div style={fieldStyle}>
                <label htmlFor="mt5-platform" style={labelStyle}>Platform</label>
                <select id="mt5-platform" value={platform} onChange={(e) => setPlatform(e.target.value as 'mt5' | 'mt4')}
                  style={inputStyle}>
                  <option value="mt5">MetaTrader 5</option>
                  <option value="mt4">MetaTrader 4</option>
                </select>
              </div>
            </div>
          )}

          <div style={{ ...fieldStyle, marginTop: 12, marginBottom: 0 }}>
            <label htmlFor="acct-label" style={labelStyle}>Label <span style={{ fontWeight: 400, opacity: 0.5 }}>optional</span></label>
            <input id="acct-label" type="text" value={label} onChange={(e) => setLabel(e.target.value)}
              style={inputStyle} placeholder={brokerType === 'deriv' ? 'My Deriv Demo' : 'My MT5 Account'} />
          </div>
          <div style={footerStyle}>
            <button type="button" onClick={onClose} style={ghostBtnStyle}>Cancel</button>
            <button type="submit" style={primaryBtnStyle}>
              <span style={{ marginRight: 6 }}>→</span>
              {brokerType === 'deriv' ? 'Connect Account' : 'Deploy Account'}
            </button>
          </div>
        </form>
      </div>
    );
  }

  // ── Deploying ────────────────────────────────────────────
  if (step === 'deploying') {
    return (
      <div style={modalCardStyle}>
        <div style={headerStyle}>
          <div>
            <h3 style={titleStyle}>{brokerType === 'deriv' ? 'Connecting Account' : 'Deploying Account'}</h3>
            <p style={subtitleStyle}>
              {brokerType === 'deriv' ? 'Connecting directly to Deriv' : 'Connecting to your broker via MetaAPI'}
            </p>
          </div>
        </div>
        {renderStepIndicator()}
        <div style={{ padding: '20px 0 8px' }}>
          <div style={progressTrackStyle}>
            <div style={{
              height: '100%', width: '40%',
              background: 'linear-gradient(90deg, var(--accent), var(--accent-hover))',
              borderRadius: 4, boxShadow: '0 0 12px var(--accent-glow)',
              animation: 'indeterminate 1.4s ease-in-out infinite',
            }} />
          </div>
          <div style={{ marginTop: 12, minHeight: 18 }}>
            <span style={{ ...progressLabelStyle, transition: 'opacity 0.3s ease', display: 'inline-block' }}>
              {deployStatus}
            </span>
          </div>
        </div>
        <style>{`@keyframes indeterminate { 0% { transform: translateX(-100%); } 100% { transform: translateX(250%); } }`}</style>
      </div>
    );
  }

  // ── Step 2: Instrument Mapping ───────────────────────────
  if (step === 'instruments') {
    return (
      <div style={modalCardStyle}>
        <div style={headerStyle}>
          <div>
            <h3 style={titleStyle}>Map Instruments</h3>
            <p style={subtitleStyle}>Set the broker symbol for each instrument on this account</p>
          </div>
          <button onClick={onClose} style={closeBtnStyle} aria-label="Close">✕</button>
        </div>
        {renderStepIndicator()}
        {formError && <div style={errorStyle} role="alert">{formError}</div>}
        <div style={{ maxHeight: 300, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8 }}>
          {loadingInstruments && (
            <div style={{ padding: '16px 0', fontSize: 11, color: 'var(--text-muted)', textAlign: 'center' }}>
              Loading instruments…
            </div>
          )}
          {!loadingInstruments && accountInstruments.length === 0 && (
            <div style={{ padding: '16px 0', fontSize: 11, color: 'var(--text-muted)', textAlign: 'center' }}>
              No instruments configured. You can add them later from the account settings.
            </div>
          )}
          {accountInstruments.map((ai) => (
            <div key={ai.instrumentId} style={{
              padding: '10px 12px', borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--glass-border)', background: 'var(--bg-surface)',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                <div style={{ flex: '0 0 auto' }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
                    {ai.instrument?.displayName || ai.instrument?.symbol || 'Unknown'}
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', marginTop: 2 }}>
                    {ai.instrument?.symbol} · {ai.instrument?.type}
                  </div>
                </div>
                <div style={{ flex: 1, maxWidth: 260 }}>
                  <label style={{ fontSize: 9, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 3, display: 'block' }}>
                    Broker Symbol
                  </label>
                  {brokerSymbolOptions.length > 0 ? (
                    <div style={{ position: 'relative' }}>
                      <input
                        type="text"
                        list={`broker-symbols-${ai.instrumentId}`}
                        value={brokerSymbolMap[ai.instrumentId] || ''}
                        onChange={(e) => setBrokerSymbolMap((prev) => ({ ...prev, [ai.instrumentId]: e.target.value }))}
                        style={{ ...inputStyle, fontSize: 11, padding: '6px 8px' }}
                        placeholder="Type or select broker symbol"
                      />
                      <datalist id={`broker-symbols-${ai.instrumentId}`}>
                        {brokerSymbolOptions.map((sym) => (
                          <option key={sym} value={sym} />
                        ))}
                      </datalist>
                    </div>
                  ) : (
                    <input
                      type="text"
                      value={brokerSymbolMap[ai.instrumentId] || ''}
                      onChange={(e) => setBrokerSymbolMap((prev) => ({ ...prev, [ai.instrumentId]: e.target.value }))}
                      style={{ ...inputStyle, fontSize: 11, padding: '6px 8px' }}
                      placeholder="e.g. Volatility 75 Index"
                    />
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
        <div style={footerStyle}>
          <button type="button" onClick={() => setStep('strategies')} style={ghostBtnStyle}>Skip</button>
          <button type="button" onClick={handleSaveInstruments} disabled={savingInstruments} style={primaryBtnStyle}>
            {savingInstruments ? 'Saving…' : 'Save & Continue'}
          </button>
        </div>
      </div>
    );
  }

  // ── Step 3: Strategies ───────────────────────────────────
  const enabledStrategies = strategies.filter((s) => s.enabled !== false);

  return (
    <div style={modalCardStyle}>
      <div style={headerStyle}>
        <div>
          <h3 style={titleStyle}>Select Strategies</h3>
          <p style={subtitleStyle}>Choose which strategies this account should run</p>
        </div>
        <button onClick={onClose} style={closeBtnStyle} aria-label="Close">✕</button>
      </div>
      {renderStepIndicator()}
      {formError && <div style={errorStyle} role="alert">{formError}</div>}
      <div style={strategyListStyle}>
        {strategiesLoading && (
          <div style={{ padding: '16px 0', fontSize: 11, color: 'var(--text-muted)', textAlign: 'center' }}>
            Loading strategies…
          </div>
        )}
        {!strategiesLoading && enabledStrategies.length === 0 && (
          <div style={{ padding: '16px 0', fontSize: 11, color: 'var(--text-muted)', textAlign: 'center' }}>
            No strategies available. Create one in the Strategies page first.
          </div>
        )}
        {enabledStrategies.map((strategy) => {
          const checked = selectedStrategies.has(strategy.id);
          const instruments = getStrategyInstruments(strategy);
          return (
            <div key={strategy.id} style={{
              ...strategyRowStyle,
              background: checked ? 'var(--accent-dim)' : 'transparent',
              borderColor: checked ? 'var(--border-glow)' : 'var(--glass-border)',
            }}>
              <label style={checkLabelStyle}>
                <div style={{
                  width: 16, height: 16, borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: checked ? 'var(--accent)' : 'transparent',
                  border: checked ? 'none' : '1.5px solid var(--text-muted)',
                  transition: 'all 0.15s ease', fontSize: 10, color: '#fff', cursor: 'pointer', flexShrink: 0,
                }}>
                  {checked && '✓'}
                </div>
                <input type="checkbox" checked={checked} onChange={() => handleToggleStrategy(strategy.id)}
                  style={{ display: 'none' }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-primary)' }}>{strategy.name}</span>
                    <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>{strategy.algorithm}</span>
                  </div>
                  {instruments.length > 0 && (
                    <div style={{ marginTop: 3, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                      {instruments.map((inst) => (
                        <span key={inst} style={{
                          fontSize: 9, padding: '1px 6px', borderRadius: 3,
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
      </div>
      <div style={footerStyle}>
        <button type="button" onClick={() => onClose()} style={ghostBtnStyle}>Skip</button>
        <button type="button" onClick={handleComplete} disabled={savingStrategies} style={primaryBtnStyle}>
          {savingStrategies ? 'Saving…' : 'Complete Setup'}
        </button>
      </div>
    </div>
  );
};


// ── Styles ──────────────────────────────────────────────────

const modalCardStyle: React.CSSProperties = {
  background: 'var(--bg-secondary)', border: '1px solid var(--glass-border)',
  borderRadius: 'var(--radius-xl)', padding: '24px',
  boxShadow: 'var(--shadow-lg), var(--shadow-glow)', position: 'relative', overflow: 'hidden',
};
const headerStyle: React.CSSProperties = {
  display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16,
};
const titleStyle: React.CSSProperties = {
  margin: 0, fontSize: 16, fontWeight: 600, color: 'var(--text-primary)', letterSpacing: '-0.3px',
};
const subtitleStyle: React.CSSProperties = {
  margin: '2px 0 0', fontSize: 12, color: 'var(--text-muted)',
};
const closeBtnStyle: React.CSSProperties = {
  background: 'var(--bg-surface)', border: '1px solid var(--glass-border)',
  borderRadius: 'var(--radius-sm)', color: 'var(--text-muted)',
  width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center',
  cursor: 'pointer', fontSize: 12, transition: 'all 0.15s ease',
};
const stepIndicatorStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 4, marginBottom: 20,
  padding: '10px 12px', background: 'var(--bg-surface)',
  borderRadius: 'var(--radius-md)', border: '1px solid var(--glass-border)',
};
const errorStyle: React.CSSProperties = {
  background: 'var(--danger-bg)', border: '1px solid var(--danger-border)',
  borderRadius: 'var(--radius-sm)', padding: '8px 12px', marginBottom: 14,
  fontSize: 11, color: 'var(--danger)',
};
const fieldsGridStyle: React.CSSProperties = {
  display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12,
};
const fieldStyle: React.CSSProperties = { marginBottom: 4 };
const labelStyle: React.CSSProperties = {
  display: 'block', fontSize: 10, fontWeight: 600, color: 'var(--text-muted)',
  marginBottom: 5, textTransform: 'uppercase', letterSpacing: '0.8px',
};
const inputStyle: React.CSSProperties = {
  width: '100%', background: 'var(--bg-surface)', border: '1px solid var(--glass-border)',
  borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)', fontSize: 12,
  padding: '8px 10px', outline: 'none', boxSizing: 'border-box',
  transition: 'border-color 0.15s ease, box-shadow 0.15s ease',
};
const hintStyle: React.CSSProperties = {
  fontSize: 10, color: 'var(--text-muted)', marginTop: 5, lineHeight: 1.4,
};
const brokerToggleStyle: React.CSSProperties = {
  display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 5,
};
const brokerOptStyle: React.CSSProperties = {
  display: 'flex', flexDirection: 'column', alignItems: 'flex-start',
  padding: '10px 12px', borderRadius: 'var(--radius-sm)',
  border: '1px solid var(--glass-border)', cursor: 'pointer',
  transition: 'all 0.15s ease', textAlign: 'left',
};
const footerStyle: React.CSSProperties = {
  display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20,
  paddingTop: 16, borderTop: '1px solid var(--glass-border)',
};
const ghostBtnStyle: React.CSSProperties = {
  background: 'none', border: '1px solid var(--glass-border)',
  borderRadius: 'var(--radius-sm)', color: 'var(--text-secondary)',
  fontSize: 12, fontWeight: 500, padding: '8px 16px', cursor: 'pointer', transition: 'all 0.15s ease',
};
const primaryBtnStyle: React.CSSProperties = {
  background: 'var(--accent)', color: '#fff', border: 'none',
  borderRadius: 'var(--radius-sm)', fontSize: 12, fontWeight: 600,
  padding: '8px 20px', cursor: 'pointer', transition: 'all 0.15s ease',
  boxShadow: '0 0 16px var(--accent-glow)',
};
const progressTrackStyle: React.CSSProperties = {
  width: '100%', height: 8, background: 'var(--bg-surface)',
  borderRadius: 4, overflow: 'hidden', border: '1px solid var(--glass-border)',
};
const progressLabelStyle: React.CSSProperties = { fontSize: 11, color: 'var(--text-secondary)' };
const strategyListStyle: React.CSSProperties = {
  maxHeight: 260, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6,
};
const strategyRowStyle: React.CSSProperties = {
  padding: '8px 10px', borderRadius: 'var(--radius-sm)',
  border: '1px solid var(--glass-border)', transition: 'all 0.15s ease',
};
const checkLabelStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer',
};

export default AddAccountForm;
