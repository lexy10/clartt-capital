import { type FC, useState, useEffect } from 'react';
import { useSandboxStore, type SandboxPosition } from '../stores/sandboxStore';
import { wsManager } from '../services/WebSocketManager';
import type { Candle } from '../types/candle';

const formatCurrency = (value: number): string =>
  value.toLocaleString('en-US', { style: 'currency', currency: 'USD' });

const pnlColor = (value: number): string => {
  if (value > 0) return 'var(--success)';
  if (value < 0) return 'var(--danger)';
  return 'var(--text-secondary)';
};

function positionPnl(pos: SandboxPosition): number {
  const diff = pos.currentPrice - pos.entryPrice;
  return pos.direction === 'BUY' ? diff * pos.size : -diff * pos.size;
}

/* ── Styles ─────────────────────────────────────────────── */

const sandboxBorder = '2px solid var(--warning)';

const containerStyle: React.CSSProperties = {
  padding: '12px',
  fontFamily: 'var(--font-sans)',
  color: 'var(--text-primary)',
};

const bannerStyle: React.CSSProperties = {
  background: 'var(--warning-bg)',
  border: '1px solid var(--warning)',
  borderRadius: 'var(--radius-sm)',
  padding: '6px 10px',
  marginBottom: '12px',
  fontSize: '12px',
  fontWeight: 600,
  color: 'var(--warning)',
  textAlign: 'center',
  letterSpacing: '0.5px',
};

const toggleBtnBase: React.CSSProperties = {
  padding: '6px 14px',
  borderRadius: 'var(--radius-sm)',
  border: 'none',
  cursor: 'pointer',
  fontSize: '12px',
  fontWeight: 600,
  transition: 'var(--transition-fast)',
};

const thStyle: React.CSSProperties = { padding: '4px 6px', fontWeight: 500 };
const tdStyle: React.CSSProperties = { padding: '4px 6px' };

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '5px 8px',
  background: 'var(--bg-tertiary)',
  border: '1px solid var(--border-primary)',
  borderRadius: 'var(--radius-sm)',
  color: 'var(--text-primary)',
  fontSize: '12px',
  boxSizing: 'border-box',
};

const selectStyle: React.CSSProperties = { ...inputStyle };

/* ── Component ──────────────────────────────────────────── */

const PortfolioSandbox: FC = () => {
  const {
    enabled,
    virtualBalance,
    positions,
    toggleSandbox,
    placeTrade,
    closePosition,
    updatePrices,
    totalUnrealizedPnl,
    portfolioValue,
  } = useSandboxStore();

  const [instrument, setInstrument] = useState('US30');
  const [direction, setDirection] = useState<'BUY' | 'SELL'>('BUY');
  const [size, setSize] = useState('1');
  const [entryPrice, setEntryPrice] = useState('');

  const handlePlace = () => {
    const s = parseFloat(size);
    const ep = parseFloat(entryPrice);
    if (isNaN(s) || s <= 0 || isNaN(ep) || ep <= 0) return;
    placeTrade({ instrument, direction, size: s, entryPrice: ep });
    setEntryPrice('');
  };

  // Subscribe to real-time candle updates to keep sandbox position prices current
  useEffect(() => {
    if (!enabled) return;
    const subId = wsManager.subscribe('candles', (candle: Candle) => {
      updatePrices({ [candle.instrument]: candle.close });
    });
    return () => {
      wsManager.unsubscribe(subId);
    };
  }, [enabled, updatePrices]);

  const unrealized = totalUnrealizedPnl();
  const total = portfolioValue();

  return (
    <div
      style={{
        ...containerStyle,
        ...(enabled ? { borderLeft: sandboxBorder } : {}),
      }}
    >
      {/* Header + toggle */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '12px',
        }}
      >
        <h3 style={{ margin: 0, fontSize: '14px', color: 'var(--text-secondary)' }}>
          Portfolio Sandbox
        </h3>
        <button
          onClick={toggleSandbox}
          style={{
            ...toggleBtnBase,
            background: enabled ? 'var(--warning)' : 'var(--bg-surface)',
            color: enabled ? '#000' : 'var(--text-secondary)',
          }}
          aria-pressed={enabled}
        >
          {enabled ? 'Exit Sandbox' : 'Enter Sandbox'}
        </button>
      </div>

      {!enabled && (
        <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          Enable sandbox mode to practice trading with virtual funds.
        </p>
      )}

      {enabled && (
        <>
          {/* Sandbox banner */}
          <div style={bannerStyle} role="status">
            ⚠ SANDBOX MODE — Trades are simulated. No real money at risk.
          </div>

          {/* Summary cards */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr 1fr',
              gap: '8px',
              marginBottom: '14px',
            }}
          >
            <Card label="Balance" value={formatCurrency(virtualBalance)} />
            <Card
              label="Unrealized P/L"
              value={formatCurrency(unrealized)}
              color={pnlColor(unrealized)}
            />
            <Card label="Portfolio Value" value={formatCurrency(total)} />
          </div>

          {/* Trade form */}
          <div
            style={{
              background: 'var(--bg-surface)',
              borderRadius: 'var(--radius-md)',
              padding: '10px',
              marginBottom: '14px',
            }}
          >
            <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '8px', color: 'var(--text-secondary)' }}>
              Place Simulated Trade
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
              <label style={labelStyle}>
                Instrument
                <input
                  style={inputStyle}
                  value={instrument}
                  onChange={(e) => setInstrument(e.target.value)}
                />
              </label>
              <label style={labelStyle}>
                Direction
                <select
                  style={selectStyle}
                  value={direction}
                  onChange={(e) => setDirection(e.target.value as 'BUY' | 'SELL')}
                >
                  <option value="BUY">BUY</option>
                  <option value="SELL">SELL</option>
                </select>
              </label>
              <label style={labelStyle}>
                Size (lots)
                <input
                  style={inputStyle}
                  type="number"
                  min="0.01"
                  step="0.01"
                  value={size}
                  onChange={(e) => setSize(e.target.value)}
                />
              </label>
              <label style={labelStyle}>
                Entry Price
                <input
                  style={inputStyle}
                  type="number"
                  min="0"
                  step="0.01"
                  value={entryPrice}
                  onChange={(e) => setEntryPrice(e.target.value)}
                  placeholder="e.g. 34250.00"
                />
              </label>
            </div>
            <button
              onClick={handlePlace}
              style={{
                ...toggleBtnBase,
                marginTop: '8px',
                width: '100%',
                background: direction === 'BUY' ? 'var(--success)' : 'var(--danger)',
                color: '#fff',
              }}
            >
              {direction} {instrument}
            </button>
          </div>

          {/* Positions table */}
          <h4 style={{ margin: '0 0 6px', fontSize: '13px', color: 'var(--text-secondary)' }}>
            Simulated Positions {positions.length > 0 && `(${positions.length})`}
          </h4>

          {positions.length === 0 && (
            <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>No open positions</p>
          )}

          {positions.length > 0 && (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                <thead>
                  <tr style={{ color: 'var(--text-muted)', textAlign: 'left' }}>
                    <th style={thStyle}>Instrument</th>
                    <th style={thStyle}>Dir</th>
                    <th style={thStyle}>Entry</th>
                    <th style={thStyle}>Current</th>
                    <th style={thStyle}>Size</th>
                    <th style={thStyle}>P/L</th>
                    <th style={thStyle}></th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((pos) => {
                    const pnl = positionPnl(pos);
                    return (
                      <tr key={pos.id} style={{ borderTop: '1px solid var(--border-secondary)' }}>
                        <td style={tdStyle}>{pos.instrument}</td>
                        <td
                          style={{
                            ...tdStyle,
                            color: pos.direction === 'BUY' ? 'var(--success)' : 'var(--danger)',
                          }}
                        >
                          {pos.direction}
                        </td>
                        <td style={tdStyle}>{pos.entryPrice.toFixed(2)}</td>
                        <td style={tdStyle}>{pos.currentPrice.toFixed(2)}</td>
                        <td style={tdStyle}>{pos.size}</td>
                        <td style={{ ...tdStyle, color: pnlColor(pnl) }}>{formatCurrency(pnl)}</td>
                        <td style={tdStyle}>
                          <button
                            onClick={() => closePosition(pos.id)}
                            style={{
                              background: 'var(--danger-bg)',
                              color: 'var(--danger)',
                              border: '1px solid var(--danger)',
                              borderRadius: 'var(--radius-sm)',
                              padding: '2px 8px',
                              cursor: 'pointer',
                              fontSize: '11px',
                            }}
                          >
                            Close
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
};

const labelStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '3px',
  fontSize: '11px',
  color: 'var(--text-muted)',
};

interface CardProps {
  label: string;
  value: string;
  color?: string;
}

const Card: FC<CardProps> = ({ label, value, color }) => (
  <div
    style={{
      background: 'var(--bg-surface)',
      borderRadius: 'var(--radius-sm)',
      padding: '8px 10px',
    }}
  >
    <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '2px' }}>{label}</div>
    <div style={{ fontSize: '14px', fontWeight: 600, color: color ?? 'var(--text-primary)' }}>
      {value}
    </div>
  </div>
);

export default PortfolioSandbox;
