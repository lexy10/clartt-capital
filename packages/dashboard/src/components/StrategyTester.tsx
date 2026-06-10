import { type FC, useState, useEffect } from 'react';
import { apiClient } from '../services/ApiClient';
import type { Strategy, BacktestConfig, BacktestResult } from '../types/api';

/* ── Styles ─────────────────────────────────────────────── */

const containerStyle: React.CSSProperties = {
  padding: '12px',
  fontFamily: 'var(--font-sans)',
  color: 'var(--text-primary)',
};

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

const labelStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '3px',
  fontSize: '11px',
  color: 'var(--text-muted)',
};

const btnStyle: React.CSSProperties = {
  padding: '8px 14px',
  borderRadius: 'var(--radius-sm)',
  border: 'none',
  cursor: 'pointer',
  fontSize: '12px',
  fontWeight: 600,
  background: 'var(--accent)',
  color: '#fff',
  width: '100%',
  transition: 'var(--transition-fast)',
};

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-surface)',
  borderRadius: 'var(--radius-sm)',
  padding: '8px 10px',
};

/* ── Stat Card ──────────────────────────────────────────── */

interface StatCardProps {
  label: string;
  value: string;
  color?: string;
}

const StatCard: FC<StatCardProps> = ({ label, value, color }) => (
  <div style={cardStyle}>
    <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '2px' }}>{label}</div>
    <div style={{ fontSize: '14px', fontWeight: 600, color: color ?? 'var(--text-primary)' }}>
      {value}
    </div>
  </div>
);

/* ── Trade Result Row ───────────────────────────────────── */

interface TradeResultRowProps {
  trade: Record<string, unknown>;
  index: number;
}

const TradeResultRow: FC<TradeResultRowProps> = ({ trade, index }) => {
  const direction = String(trade.direction ?? '');
  const entry = Number(trade.entry_price ?? 0);
  const exit = Number(trade.exit_price ?? 0);
  const pnl = Number(trade.profit_loss ?? 0);
  const rr = trade.reward_risk != null ? Number(trade.reward_risk) : null;

  return (
    <tr style={{ borderTop: '1px solid var(--border-secondary)' }}>
      <td style={tdStyle}>{index + 1}</td>
      <td
        style={{
          ...tdStyle,
          color: direction === 'BUY' ? 'var(--success)' : 'var(--danger)',
        }}
      >
        {direction}
      </td>
      <td style={tdStyle}>{entry.toFixed(2)}</td>
      <td style={tdStyle}>{exit.toFixed(2)}</td>
      <td
        style={{
          ...tdStyle,
          color: pnl > 0 ? 'var(--success)' : pnl < 0 ? 'var(--danger)' : 'var(--text-secondary)',
        }}
      >
        {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
      </td>
      <td
        style={{
          ...tdStyle,
          color: rr != null && rr > 0 ? 'var(--success)' : rr != null && rr < 0 ? 'var(--danger)' : 'var(--text-secondary)',
        }}
      >
        {rr != null ? rr.toFixed(2) : '—'}
      </td>
    </tr>
  );
};

const thStyle: React.CSSProperties = { padding: '4px 6px', fontWeight: 500 };
const tdStyle: React.CSSProperties = { padding: '4px 6px' };

/* ── Main Component ─────────────────────────────────────── */

const StrategyTester: FC = () => {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [selectedStrategyId, setSelectedStrategyId] = useState('');
  const [instrument, setInstrument] = useState('US30');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  // Backtest engine params
  const [initialCapital, setInitialCapital] = useState('10000');
  const [spread, setSpread] = useState('0');
  const [slippage, setSlippage] = useState('0');
  const [maxLotSize, setMaxLotSize] = useState('10');
  const [commission, setCommission] = useState('0');
  // Algorithm param overrides (JSON)
  const [algoParams, setAlgoParams] = useState('{}');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);

  useEffect(() => {
    apiClient.strategies.list().then(setStrategies).catch(() => {
      /* strategies will remain empty */
    });
  }, []);

  const handleRunBacktest = async () => {
    if (!selectedStrategyId) return;

    let parsedAlgoParams: Record<string, unknown> = {};
    try {
      parsedAlgoParams = JSON.parse(algoParams);
    } catch {
      setError('Invalid JSON in algorithm parameters field');
      return;
    }

    // Merge backtest engine params + algorithm overrides into one parameters object.
    // The backend splits them by key name.
    const parameters: Record<string, unknown> = {
      initial_capital: parseFloat(initialCapital) || 10000,
      spread: parseFloat(spread) || 0,
      slippage: parseFloat(slippage) || 0,
      max_lot_size: parseFloat(maxLotSize) || 10,
      commission_per_trade: parseFloat(commission) || 0,
      ...parsedAlgoParams,
    };

    const config: BacktestConfig = {
      strategy_id: selectedStrategyId,
      instrument,
      start_date: startDate,
      end_date: endDate,
      parameters,
    };

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const backtestResult = await apiClient.strategies.runBacktest(config);
      setResult(backtestResult);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Backtest failed';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  const pnlColor = (v: number) =>
    v > 0 ? 'var(--success)' : v < 0 ? 'var(--danger)' : 'var(--text-secondary)';

  return (
    <div style={containerStyle}>
      <h3 style={{ margin: '0 0 12px', fontSize: '14px', color: 'var(--text-secondary)' }}>
        Strategy Tester
      </h3>

      {error && (
        <p style={{ color: 'var(--danger)', fontSize: '12px', margin: '0 0 8px' }}>{error}</p>
      )}

      {/* Configuration form */}
      <div
        style={{
          background: 'var(--bg-surface)',
          borderRadius: 'var(--radius-md)',
          padding: '10px',
          marginBottom: '14px',
        }}
      >
        <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '8px', color: 'var(--text-secondary)' }}>
          Backtest Configuration
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
          <label style={labelStyle}>
            Strategy
            <select
              style={selectStyle}
              value={selectedStrategyId}
              onChange={(e) => setSelectedStrategyId(e.target.value)}
              aria-label="Select strategy"
            >
              <option value="">Select a strategy</option>
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </label>
          <label style={labelStyle}>
            Instrument
            <input
              style={inputStyle}
              value={instrument}
              onChange={(e) => setInstrument(e.target.value)}
            />
          </label>
          <label style={labelStyle}>
            Start Date
            <input
              style={inputStyle}
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </label>
          <label style={labelStyle}>
            End Date
            <input
              style={inputStyle}
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
            />
          </label>
        </div>

        <div style={{ fontSize: '11px', fontWeight: 600, margin: '8px 0 4px', color: 'var(--text-muted)' }}>
          Simulation Parameters
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
          <label style={labelStyle}>
            Initial Capital ($)
            <input
              style={inputStyle}
              type="number"
              min="100"
              value={initialCapital}
              onChange={(e) => setInitialCapital(e.target.value)}
            />
          </label>
          <label style={labelStyle}>
            Max Lot Size
            <input
              style={inputStyle}
              type="number"
              min="0.01"
              step="0.01"
              value={maxLotSize}
              onChange={(e) => setMaxLotSize(e.target.value)}
            />
          </label>
          <label style={labelStyle}>
            Spread (points)
            <input
              style={inputStyle}
              type="number"
              min="0"
              step="0.1"
              value={spread}
              onChange={(e) => setSpread(e.target.value)}
            />
          </label>
          <label style={labelStyle}>
            Slippage (points)
            <input
              style={inputStyle}
              type="number"
              min="0"
              step="0.1"
              value={slippage}
              onChange={(e) => setSlippage(e.target.value)}
            />
          </label>
          <label style={{ ...labelStyle, gridColumn: '1 / -1' }}>
            Commission per Trade ($)
            <input
              style={inputStyle}
              type="number"
              min="0"
              step="0.01"
              value={commission}
              onChange={(e) => setCommission(e.target.value)}
            />
          </label>
        </div>

        <div style={{ fontSize: '11px', fontWeight: 600, margin: '8px 0 4px', color: 'var(--text-muted)' }}>
          Algorithm Param Overrides (JSON)
        </div>
        <label style={{ ...labelStyle, gridColumn: '1 / -1' }}>
          <input
            style={inputStyle}
            value={algoParams}
            onChange={(e) => setAlgoParams(e.target.value)}
            placeholder='{"swing_length": 5, "max_rr_cap": 5.0}'
          />
        </label>

        <button
          onClick={handleRunBacktest}
          disabled={loading || !selectedStrategyId}
          style={{
            ...btnStyle,
            marginTop: '8px',
            opacity: loading || !selectedStrategyId ? 0.5 : 1,
          }}
          aria-busy={loading}
        >
          {loading ? 'Running Backtest…' : 'Run Backtest'}
        </button>
      </div>

      {/* Results */}
      {result && (
        <>
          {/* Performance statistics */}
          <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '6px', color: 'var(--text-secondary)' }}>
            Performance Statistics
          </div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr 1fr',
              gap: '8px',
              marginBottom: '14px',
            }}
          >
            <StatCard label="Win Rate" value={`${((result.win_rate ?? 0) * 100).toFixed(1)}%`} />
            <StatCard
              label="Max Drawdown"
              value={`${(result.max_drawdown ?? 0).toFixed(2)}`}
              color="var(--danger)"
            />
            <StatCard label="Sharpe Ratio" value={(result.sharpe_ratio ?? 0).toFixed(2)} />
            <StatCard
              label="Profit Factor"
              value={(result.profit_factor ?? 0).toFixed(2)}
              color={pnlColor((result.profit_factor ?? 0) - 1)}
            />
            <StatCard
              label="Expectancy"
              value={(result.expectancy ?? 0).toFixed(2)}
              color={pnlColor(result.expectancy ?? 0)}
            />
            <StatCard label="Total Trades" value={String(result.total_trades ?? 0)} />
            <StatCard
              label="Avg R:R"
              value={(result.average_rr ?? result.averageRr ?? 0).toFixed(2)}
              color={pnlColor(result.average_rr ?? result.averageRr ?? 0)}
            />
            <StatCard
              label="Net Profit"
              value={`$${(result.net_profit ?? result.netProfit ?? 0).toFixed(2)}`}
              color={pnlColor(result.net_profit ?? result.netProfit ?? 0)}
            />
          </div>

          {/* Trade results table */}
          {(result.trade_results ?? []).length > 0 && (
            <>
              <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '6px', color: 'var(--text-secondary)' }}>
                Trade Results
              </div>
              <div style={{ overflowX: 'auto', maxHeight: '200px', overflowY: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                  <thead>
                    <tr style={{ color: 'var(--text-muted)', textAlign: 'left' }}>
                      <th style={thStyle}>#</th>
                      <th style={thStyle}>Dir</th>
                      <th style={thStyle}>Entry</th>
                      <th style={thStyle}>Exit</th>
                      <th style={thStyle}>P/L</th>
                      <th style={thStyle}>R:R</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(result.trade_results ?? []).map((trade, i) => (
                      <TradeResultRow
                        key={i}
                        trade={trade as Record<string, unknown>}
                        index={i}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
};

export default StrategyTester;
