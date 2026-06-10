import { type FC, useState } from 'react';
import {
  useIndicatorStore,
  type IndicatorType,
} from '../stores/indicatorStore';

const INDICATOR_META: {
  type: IndicatorType;
  label: string;
  description: string;
  paramLabels: Record<string, string>;
}[] = [
  {
    type: 'MA',
    label: 'Moving Average',
    description: 'Simple moving average overlay',
    paramLabels: { period: 'Period' },
  },
  {
    type: 'RSI',
    label: 'RSI',
    description: 'Relative Strength Index',
    paramLabels: { period: 'Period' },
  },
  {
    type: 'MACD',
    label: 'MACD',
    description: 'Moving Average Convergence Divergence',
    paramLabels: { fast: 'Fast', slow: 'Slow', signal: 'Signal' },
  },
  {
    type: 'BB',
    label: 'Bollinger Bands',
    description: 'Volatility bands around MA',
    paramLabels: { period: 'Period', stdDev: 'Std Dev' },
  },
];

const COMPARE_COLORS = ['#58a6ff', '#d29922', '#a371f7', '#f778ba'];

const IndicatorPanel: FC = () => {
  const indicators = useIndicatorStore((s) => s.indicators);
  const toggleIndicator = useIndicatorStore((s) => s.toggleIndicator);
  const setIndicatorParam = useIndicatorStore((s) => s.setIndicatorParam);
  const compareInstruments = useIndicatorStore((s) => s.compareInstruments);
  const addCompareInstrument = useIndicatorStore((s) => s.addCompareInstrument);
  const removeCompareInstrument = useIndicatorStore(
    (s) => s.removeCompareInstrument
  );

  const [compareInput, setCompareInput] = useState('');

  const handleAddCompare = () => {
    const symbol = compareInput.trim().toUpperCase();
    if (!symbol) return;
    const color =
      COMPARE_COLORS[compareInstruments.length % COMPARE_COLORS.length];
    addCompareInstrument(symbol, color);
    setCompareInput('');
  };

  return (
    <div className="indicator-panel">
      <div className="panel-section">
        <h3 className="panel-title">Indicators</h3>
        <ul className="indicator-list" role="list">
          {INDICATOR_META.map(({ type, label, description, paramLabels }) => {
            const config = indicators[type];
            return (
              <li key={type} className="indicator-item">
                <div className="indicator-header">
                  <button
                    className={`indicator-toggle${config.enabled ? ' indicator-toggle-on' : ''}`}
                    onClick={() => toggleIndicator(type)}
                    aria-pressed={config.enabled}
                    aria-label={`Toggle ${label}`}
                    title={description}
                  >
                    <span className="indicator-label">{label}</span>
                    <span className="indicator-badge mono">
                      {config.enabled ? 'ON' : 'OFF'}
                    </span>
                  </button>
                </div>
                {config.enabled && (
                  <div className="indicator-params">
                    {Object.entries(paramLabels).map(([key, paramLabel]) => (
                      <label key={key} className="indicator-param">
                        <span className="indicator-param-label">
                          {paramLabel}
                        </span>
                        <input
                          type="number"
                          className="indicator-param-input mono"
                          value={config.params[key]}
                          min={1}
                          step={key === 'stdDev' ? 0.5 : 1}
                          onChange={(e) =>
                            setIndicatorParam(
                              type,
                              key,
                              Number(e.target.value)
                            )
                          }
                          aria-label={`${label} ${paramLabel}`}
                        />
                      </label>
                    ))}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </div>

      <div className="panel-section">
        <h3 className="panel-title">Compare</h3>
        <div className="compare-add">
          <input
            type="text"
            className="compare-input mono"
            placeholder="Symbol (e.g. SPX500)"
            value={compareInput}
            onChange={(e) => setCompareInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAddCompare()}
            aria-label="Add instrument to compare"
          />
          <button
            className="compare-btn"
            onClick={handleAddCompare}
            aria-label="Add compare instrument"
          >
            +
          </button>
        </div>
        {compareInstruments.length > 0 && (
          <ul className="compare-list" role="list">
            {compareInstruments.map(({ symbol, color }) => (
              <li key={symbol} className="compare-item">
                <span
                  className="compare-dot"
                  style={{ backgroundColor: color }}
                />
                <span className="compare-symbol mono">{symbol}</span>
                <button
                  className="compare-remove"
                  onClick={() => removeCompareInstrument(symbol)}
                  aria-label={`Remove ${symbol}`}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
};

export default IndicatorPanel;
