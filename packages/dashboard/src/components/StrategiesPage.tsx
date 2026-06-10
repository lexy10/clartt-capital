import { type FC, type ReactNode, useEffect, useState, useCallback, useRef } from 'react';
import { useStrategyStore } from '../stores/strategyStore';
import { useInstrumentStore } from '../stores/instrumentStore';
import { wsManager } from '../services/WebSocketManager';
import type { Strategy, BacktestUpdateEvent, BacktestResult, AlgorithmInfo, BacktestTrade } from '../types/api';
import { apiClient } from '../services/ApiClient';
import {
  createChart,
  ColorType,
  CrosshairMode,
  type IChartApi,
  type CandlestickData,
  type SeriesMarker,
  type Time,
} from 'lightweight-charts';

/* ── Helpers ── */

function fmtDate(iso: string | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
    ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function fmtFullDate(iso: string | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) +
    ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function fmtShortDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
    ' \'' + String(d.getFullYear()).slice(-2);
}

function formatPct(v: number | string): string {
  const n = Number(v);
  if (isNaN(n)) return '—';
  return (n * 100).toFixed(1) + '%';
}

function formatNum(v: number | string): string {
  const n = Number(v);
  if (isNaN(n)) return '—';
  return n.toFixed(2);
}

const TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1d'] as const;
const ICT_ALGORITHM = 'ict_order_block';

const ICT_DEFAULT_PARAMS: Record<string, unknown> = {
  structure_lookback: 20,
  trend_lookback: 50,
  swing_length: 5,
  max_rr_cap: 5.0,
  cooldown_candles: 6,
  max_candle_size_multiplier: 2.0,
  kill_zone_mode: 'disabled',
  kill_zones: [],
  kill_zone_confidence_penalty: 0.15,
  choch_lookback: 3,
  zone_filter_enabled: true,
  breaker_blocks_enabled: true,
  ob_max_age_candles: 500,
};

const ICT_SELECTIVITY_PRESETS: { label: string; params: Record<string, unknown> }[] = [
  {
    label: 'Balanced',
    params: ICT_DEFAULT_PARAMS,
  },
  {
    label: 'Selective',
    params: {
      structure_lookback: 30,
      trend_lookback: 75,
      swing_length: 6,
      max_rr_cap: 4.0,
      cooldown_candles: 12,
      max_candle_size_multiplier: 1.8,
      kill_zone_mode: 'soft',
      kill_zone_confidence_penalty: 0.2,
      choch_lookback: 4,
      zone_filter_enabled: true,
      breaker_blocks_enabled: true,
      ob_max_age_candles: 350,
    },
  },
  {
    label: 'Strict',
    params: {
      structure_lookback: 40,
      trend_lookback: 100,
      swing_length: 8,
      max_rr_cap: 3.0,
      cooldown_candles: 18,
      max_candle_size_multiplier: 1.5,
      kill_zone_mode: 'strict',
      kill_zone_confidence_penalty: 0.25,
      choch_lookback: 5,
      zone_filter_enabled: true,
      breaker_blocks_enabled: true,
      ob_max_age_candles: 250,
    },
  },
];

function formatAlgorithmName(name: string): string {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function defaultParamsForAlgorithm(algorithmName: string, algorithms: AlgorithmInfo[]): Record<string, unknown> {
  const alg = algorithms.find((a) => a.name === algorithmName);
  if (algorithmName === ICT_ALGORITHM) {
    return { ...ICT_DEFAULT_PARAMS, ...(alg?.default_params ?? {}) };
  }
  return alg ? { ...alg.default_params } : {};
}

function normalizeIctParams(params: Record<string, unknown> | undefined): Record<string, unknown> {
  return { ...ICT_DEFAULT_PARAMS, ...(params ?? {}) };
}

function numberParam(params: Record<string, unknown>, key: string, fallback: number): number {
  const raw = params[key] ?? ICT_DEFAULT_PARAMS[key] ?? fallback;
  const n = Number(raw);
  return Number.isFinite(n) ? n : fallback;
}

function boolParam(params: Record<string, unknown>, key: string, fallback: boolean): boolean {
  const raw = params[key] ?? ICT_DEFAULT_PARAMS[key] ?? fallback;
  return typeof raw === 'boolean' ? raw : fallback;
}

function textParam(params: Record<string, unknown>, key: string, fallback: string): string {
  const raw = params[key] ?? ICT_DEFAULT_PARAMS[key] ?? fallback;
  return typeof raw === 'string' ? raw : fallback;
}

/* ── Sub-components ── */

const DetailRow: FC<{ label: string; value?: string; mono?: boolean; children?: ReactNode }> = ({ label, value, mono, children }) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid var(--border-primary)' }}>
    <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{label}</span>
    {children ?? (
      <span style={{ fontSize: 12, fontFamily: mono ? 'var(--font-mono)' : 'var(--font-sans)', color: 'var(--text-primary)' }}>
        {value}
      </span>
    )}
  </div>
);

const MetricCard: FC<{ label: string; value: string }> = ({ label, value }) => (
  <div style={metricCardStyle}>
    <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>{label}</div>
    <div style={{ fontSize: 16, fontWeight: 600, fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>{value}</div>
  </div>
);

/* ── Toggle Switch ── */
const ToggleSwitch: FC<{ checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label?: string }> = ({ checked, onChange, disabled, label }) => (
  <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.5 : 1 }}>
    <div
      onClick={(e) => { e.preventDefault(); if (!disabled) onChange(!checked); }}
      style={{
        width: 36, height: 20, borderRadius: 10, position: 'relative',
        background: checked ? 'var(--accent, #3b82f6)' : 'var(--border-primary)',
        transition: 'background 0.2s',
      }}
      role="switch"
      aria-checked={checked}
    >
      <div style={{
        width: 16, height: 16, borderRadius: '50%', background: '#fff',
        position: 'absolute', top: 2, left: checked ? 18 : 2,
        transition: 'left 0.2s', boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
      }} />
    </div>
    {label && <span style={{ fontSize: 12, color: 'var(--text-primary)' }}>{label}</span>}
  </label>
);

/* ── Multi-select chips ── */
const MultiSelect: FC<{ options: { value: string; label: string }[]; selected: string[]; onChange: (v: string[]) => void }> = ({ options, selected, onChange }) => (
  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
    {options.map((opt) => {
      const active = selected.includes(opt.value);
      return (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(active ? selected.filter((s) => s !== opt.value) : [...selected, opt.value])}
          style={{
            padding: '4px 10px', fontSize: 11, fontWeight: 500, borderRadius: 12,
            border: active ? '1px solid var(--accent)' : '1px solid var(--border-primary)',
            background: active ? 'var(--accent)' : 'transparent',
            color: active ? '#fff' : 'var(--text-secondary)',
            cursor: 'pointer', transition: 'all 0.15s',
          }}
        >
          {opt.label}
        </button>
      );
    })}
  </div>
);

const IctSlider: FC<{
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  decimals?: number;
}> = ({ label, value, min, max, step, onChange, decimals }) => {
  const cleanValue = Number.isFinite(value) ? value : min;
  const shown = decimals != null ? cleanValue.toFixed(decimals) : String(cleanValue);
  const handleChange = (raw: string) => {
    const next = Number(raw);
    if (Number.isFinite(next)) onChange(step >= 1 ? Math.round(next) : next);
  };

  return (
    <label style={formLabelStyle}>
      <span style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
        <span>{label}</span>
        <span style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>{shown}</span>
      </span>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 78px', gap: 8, alignItems: 'center' }}>
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={cleanValue}
          onChange={(e) => handleChange(e.target.value)}
        />
        <input
          type="number"
          min={min}
          max={max}
          step={step}
          value={shown}
          onChange={(e) => handleChange(e.target.value)}
          style={{ ...inputStyle, textAlign: 'right' }}
        />
      </div>
    </label>
  );
};

const IctSelectivityEditor: FC<{
  params: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  onApplyPreset: (params: Record<string, unknown>) => void;
}> = ({ params, onChange, onApplyPreset }) => {
  const p = normalizeIctParams(params);

  return (
    <div style={{ borderTop: '1px solid var(--border-primary)', marginTop: 16, paddingTop: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 12 }}>
        <h4 style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', margin: 0 }}>ICT Selectivity</h4>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {ICT_SELECTIVITY_PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              onClick={() => onApplyPreset(preset.params)}
              style={{
                padding: '4px 10px',
                fontSize: 11,
                fontWeight: 600,
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-primary)',
                background: 'var(--bg-primary)',
                color: 'var(--text-secondary)',
                cursor: 'pointer',
              }}
            >
              {preset.label}
            </button>
          ))}
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
        <IctSlider label="Structure Lookback" min={5} max={100} step={1} value={numberParam(p, 'structure_lookback', 20)} onChange={(v) => onChange('structure_lookback', v)} />
        <IctSlider label="Trend Lookback" min={10} max={200} step={1} value={numberParam(p, 'trend_lookback', 50)} onChange={(v) => onChange('trend_lookback', v)} />
        <IctSlider label="Swing Length" min={2} max={20} step={1} value={numberParam(p, 'swing_length', 5)} onChange={(v) => onChange('swing_length', v)} />
        <IctSlider label="Max R:R Cap" min={1} max={50} step={0.5} decimals={1} value={numberParam(p, 'max_rr_cap', 5)} onChange={(v) => onChange('max_rr_cap', v)} />
        <IctSlider label="Zone Cooldown" min={1} max={100} step={1} value={numberParam(p, 'cooldown_candles', 6)} onChange={(v) => onChange('cooldown_candles', v)} />
        <IctSlider label="Max OB Candle Size" min={0.5} max={10} step={0.1} decimals={1} value={numberParam(p, 'max_candle_size_multiplier', 2)} onChange={(v) => onChange('max_candle_size_multiplier', v)} />
        <IctSlider label="CHOCH Lookback" min={1} max={10} step={1} value={numberParam(p, 'choch_lookback', 3)} onChange={(v) => onChange('choch_lookback', v)} />
        <IctSlider label="OB Max Age" min={50} max={5000} step={50} value={numberParam(p, 'ob_max_age_candles', 500)} onChange={(v) => onChange('ob_max_age_candles', v)} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12, marginTop: 12 }}>
        <label style={formLabelStyle}>
          Kill Zone Mode
          <select value={textParam(p, 'kill_zone_mode', 'disabled')} onChange={(e) => onChange('kill_zone_mode', e.target.value)} style={inputStyle}>
            <option value="disabled">Disabled</option>
            <option value="soft">Soft</option>
            <option value="strict">Strict</option>
          </select>
        </label>
        <IctSlider label="Kill Zone Penalty" min={0} max={0.5} step={0.05} decimals={2} value={numberParam(p, 'kill_zone_confidence_penalty', 0.15)} onChange={(v) => onChange('kill_zone_confidence_penalty', v)} />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, justifyContent: 'end' }}>
          <ToggleSwitch checked={boolParam(p, 'zone_filter_enabled', true)} onChange={(v) => onChange('zone_filter_enabled', v)} label="Premium/Discount Filter" />
          <ToggleSwitch checked={boolParam(p, 'breaker_blocks_enabled', true)} onChange={(v) => onChange('breaker_blocks_enabled', v)} label="Breaker Blocks" />
        </div>
      </div>
    </div>
  );
};

const IctSelectivitySummary: FC<{ params: Record<string, unknown> }> = ({ params }) => {
  const p = normalizeIctParams(params);
  return (
    <>
      <h4 style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', margin: '12px 0 6px' }}>ICT Selectivity</h4>
      <DetailRow label="Structure / Trend Lookback" value={`${numberParam(p, 'structure_lookback', 20)} / ${numberParam(p, 'trend_lookback', 50)}`} mono />
      <DetailRow label="Swing / CHOCH Lookback" value={`${numberParam(p, 'swing_length', 5)} / ${numberParam(p, 'choch_lookback', 3)}`} mono />
      <DetailRow label="Max R:R Cap" value={String(numberParam(p, 'max_rr_cap', 5))} mono />
      <DetailRow label="Zone Cooldown" value={`${numberParam(p, 'cooldown_candles', 6)} candles`} mono />
      <DetailRow label="Max OB Candle Size" value={`${numberParam(p, 'max_candle_size_multiplier', 2)}x median`} mono />
      <DetailRow label="Kill Zone Mode" value={textParam(p, 'kill_zone_mode', 'disabled')} mono />
      <DetailRow label="Premium/Discount Filter" value={boolParam(p, 'zone_filter_enabled', true) ? 'On' : 'Off'} mono />
      <DetailRow label="Breaker Blocks" value={boolParam(p, 'breaker_blocks_enabled', true) ? 'On' : 'Off'} mono />
    </>
  );
};

/* ── Main Component ── */

const StrategiesPage: FC = () => {
  const {
    strategies, loading, error, selectedStrategyId,
    algorithms,
    backtestLoading, backtestError, lastBacktestResult,
    backtestHistory, backtestHistoryLoading,
    backtestsByStrategy, updateBacktestFromWS,
    fetchStrategies, fetchAlgorithms, updateStrategy,
    createStrategy, deleteStrategy,
    selectStrategy, clearSelection,
    runBacktest, clearBacktestResult, fetchBacktestHistory,
  } = useStrategyStore();

  const [showBacktestForm, setShowBacktestForm] = useState(false);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createAlgorithm, setCreateAlgorithm] = useState(ICT_ALGORITHM);
  const [createInstruments, setCreateInstruments] = useState<string[]>([]);
  const [createEntryTf, setCreateEntryTf] = useState('30m');
  const [createHigherTf, setCreateHigherTf] = useState('1h');
  const [createTrendTf, setCreateTrendTf] = useState('4h');
  const [createMinConfidence, setCreateMinConfidence] = useState('0.6');
  const [createEnabled, setCreateEnabled] = useState(true);
  const [createAlgorithmParams, setCreateAlgorithmParams] = useState<Record<string, unknown>>({ ...ICT_DEFAULT_PARAMS });
  const [createMinRR, setCreateMinRR] = useState('2.0');
  const [createMaxRisk, setCreateMaxRisk] = useState('2.0');
  const [createMaxDailyLoss, setCreateMaxDailyLoss] = useState('5.0');
  const [createMaxSpread, setCreateMaxSpread] = useState('50.0');
  const [createMaxSlippage, setCreateMaxSlippage] = useState('10.0');
  const [createVolMultiplier, setCreateVolMultiplier] = useState('1.5');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // Exit rules state for create form
  const [createTrailingEnabled, setCreateTrailingEnabled] = useState(false);
  const [createTrailingActivation, setCreateTrailingActivation] = useState('20');
  const [createTrailingDistance, setCreateTrailingDistance] = useState('10');
  const [createBreakEvenEnabled, setCreateBreakEvenEnabled] = useState(false);
  const [createBreakEvenActivation, setCreateBreakEvenActivation] = useState('15');
  const [createBreakEvenBuffer, setCreateBreakEvenBuffer] = useState('2');
  const [createTimeExitEnabled, setCreateTimeExitEnabled] = useState(false);
  const [createTimeExitMinutes, setCreateTimeExitMinutes] = useState('240');
  const [createPartialCloseEnabled, setCreatePartialCloseEnabled] = useState(false);
  const [createPartialTrigger, setCreatePartialTrigger] = useState('30');
  const [createPartialPercent, setCreatePartialPercent] = useState('50');
  const [deleting, setDeleting] = useState(false);
  const [btInstrument, setBtInstrument] = useState('');
  const [btStartDate, setBtStartDate] = useState('');
  const [btEndDate, setBtEndDate] = useState('');

  // Algorithm editing state
  const [editingAlgorithm, setEditingAlgorithm] = useState(false);
  const [selectedAlgorithm, setSelectedAlgorithm] = useState('');
  const [algorithmParams, setAlgorithmParams] = useState<Record<string, unknown>>({});
  const [updateError, setUpdateError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Strategy settings editing state
  const [editingSettings, setEditingSettings] = useState(false);
  const [settingsInstruments, setSettingsInstruments] = useState<string[]>([]);
  const [settingsEntryTf, setSettingsEntryTf] = useState('30m');
  const [settingsHigherTf, setSettingsHigherTf] = useState('1h');
  const [settingsTrendTf, setSettingsTrendTf] = useState('4h');
  const [settingsMinConfidence, setSettingsMinConfidence] = useState('0.6');
  const [settingsMinRR, setSettingsMinRR] = useState('2.0');
  const [settingsMaxRisk, setSettingsMaxRisk] = useState('2.0');
  const [settingsMaxDailyLoss, setSettingsMaxDailyLoss] = useState('5.0');
  const [settingsMaxSpread, setSettingsMaxSpread] = useState('50.0');
  const [settingsMaxSlippage, setSettingsMaxSlippage] = useState('10.0');
  const [settingsVolMultiplier, setSettingsVolMultiplier] = useState('1.5');
  const [savingSettings, setSavingSettings] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);

  // Exit rules state for edit settings
  const [settingsTrailingEnabled, setSettingsTrailingEnabled] = useState(false);
  const [settingsTrailingActivation, setSettingsTrailingActivation] = useState('20');
  const [settingsTrailingDistance, setSettingsTrailingDistance] = useState('10');
  const [settingsBreakEvenEnabled, setSettingsBreakEvenEnabled] = useState(false);
  const [settingsBreakEvenActivation, setSettingsBreakEvenActivation] = useState('15');
  const [settingsBreakEvenBuffer, setSettingsBreakEvenBuffer] = useState('2');
  const [settingsTimeExitEnabled, setSettingsTimeExitEnabled] = useState(false);
  const [settingsTimeExitMinutes, setSettingsTimeExitMinutes] = useState('240');
  const [settingsPartialCloseEnabled, setSettingsPartialCloseEnabled] = useState(false);
  const [settingsPartialTrigger, setSettingsPartialTrigger] = useState('30');
  const [settingsPartialPercent, setSettingsPartialPercent] = useState('50');
  const [togglingEnabled, setTogglingEnabled] = useState(false);
  const [detailBacktest, setDetailBacktest] = useState<BacktestResult | null>(null);

  const { instruments, loading: instrumentsLoading, error: instrumentsError, fetchInstruments } = useInstrumentStore();

  const selectedStrategy = strategies.find((s) => s.id === selectedStrategyId) ?? null;

  useEffect(() => {
    fetchStrategies();
    fetchAlgorithms();
    fetchInstruments();
  }, [fetchStrategies, fetchAlgorithms, fetchInstruments]);

  useEffect(() => {
    if (instruments.length > 0 && !btInstrument) {
      setBtInstrument(instruments[0].symbol);
    }
  }, [instruments]);

  useEffect(() => {
    const subId = wsManager.subscribe('backtest', (data: BacktestUpdateEvent) => {
      updateBacktestFromWS(data);
    });
    return () => { wsManager.unsubscribe(subId); };
  }, [updateBacktestFromWS]);

  useEffect(() => {
    if (selectedStrategyId) fetchBacktestHistory(selectedStrategyId);
  }, [selectedStrategyId, fetchBacktestHistory]);

  // Initialize editing states when strategy is selected
  useEffect(() => {
    if (selectedStrategy) {
      setSelectedAlgorithm(selectedStrategy.algorithm ?? '');
      const config = selectedStrategy.config ?? {};
      const savedParams = (config.algorithm_params as Record<string, unknown>) ?? {};
      setAlgorithmParams(
        selectedStrategy.algorithm === ICT_ALGORITHM
          ? { ...defaultParamsForAlgorithm(ICT_ALGORITHM, algorithms), ...savedParams }
          : savedParams,
      );
      setEditingAlgorithm(false);
      setUpdateError(null);

      // Settings state
      setSettingsInstruments((config.instruments ?? []) as string[]);
      setSettingsEntryTf(String(config.entry_timeframe ?? '30m'));
      setSettingsHigherTf(String(config.higher_timeframe ?? '1h'));
      setSettingsTrendTf(String(config.trend_timeframe ?? '4h'));
      setSettingsMinConfidence(String(config.min_confidence_score ?? '0.6'));
      const risk = (config.risk_settings ?? {}) as Record<string, unknown>;
      setSettingsMinRR(String(risk.min_reward_risk_ratio ?? '2.0'));
      setSettingsMaxRisk(String(risk.max_risk_per_trade_pct ?? '2.0'));
      setSettingsMaxDailyLoss(String(risk.max_daily_loss_pct ?? '5.0'));
      setSettingsMaxSpread(String(risk.max_spread ?? '50.0'));
      setSettingsMaxSlippage(String(risk.max_slippage ?? '10.0'));
      setSettingsVolMultiplier(String(risk.volatility_multiplier ?? '1.5'));
      setEditingSettings(false);
      setSettingsError(null);

      // Exit rules state
      const exitRules = (config.exit_rules ?? {}) as Record<string, unknown>;
      const trailing = (exitRules.trailing_stop ?? {}) as Record<string, unknown>;
      const breakEven = (exitRules.break_even ?? {}) as Record<string, unknown>;
      const timeExit = (exitRules.time_exit ?? {}) as Record<string, unknown>;
      const partial = (exitRules.partial_close ?? {}) as Record<string, unknown>;
      setSettingsTrailingEnabled(Boolean(trailing.enabled));
      setSettingsTrailingActivation(String(trailing.activation_pips ?? '20'));
      setSettingsTrailingDistance(String(trailing.trail_distance_pips ?? '10'));
      setSettingsBreakEvenEnabled(Boolean(breakEven.enabled));
      setSettingsBreakEvenActivation(String(breakEven.activation_pips ?? '15'));
      setSettingsBreakEvenBuffer(String(breakEven.buffer_pips ?? '2'));
      setSettingsTimeExitEnabled(Boolean(timeExit.enabled));
      setSettingsTimeExitMinutes(String(timeExit.max_duration_minutes ?? '240'));
      setSettingsPartialCloseEnabled(Boolean(partial.enabled));
      setSettingsPartialTrigger(String(partial.trigger_pips ?? '30'));
      setSettingsPartialPercent(String(partial.close_percent ?? '50'));
    }
  }, [selectedStrategy?.id, algorithms]);

  const handleSelectStrategy = (id: string) => {
    selectStrategy(id);
    clearBacktestResult();
    setShowBacktestForm(false);
  };

  const handleBack = () => {
    clearSelection();
    clearBacktestResult();
    setShowBacktestForm(false);
    setEditingAlgorithm(false);
    setEditingSettings(false);
    setUpdateError(null);
    setSettingsError(null);
  };

  const handleRunBacktest = () => {
    if (!selectedStrategy) return;
    runBacktest({
      strategy_id: selectedStrategy.id,
      instrument: btInstrument,
      start_date: btStartDate,
      end_date: btEndDate,
      parameters: {},
    });
  };

  const handleAlgorithmChange = useCallback((newAlgorithm: string) => {
    setSelectedAlgorithm(newAlgorithm);
    setAlgorithmParams(defaultParamsForAlgorithm(newAlgorithm, algorithms));
  }, [algorithms]);

  const handleParamChange = useCallback((key: string, value: unknown) => {
    setAlgorithmParams((prev) => ({ ...prev, [key]: value }));
  }, []);

  const handleApplyIctPreset = useCallback((params: Record<string, unknown>) => {
    setAlgorithmParams((prev) => ({ ...prev, ...params }));
  }, []);

  const handleCreateAlgorithmChange = useCallback((newAlgorithm: string) => {
    setCreateAlgorithm(newAlgorithm);
    setCreateAlgorithmParams(defaultParamsForAlgorithm(newAlgorithm, algorithms));
  }, [algorithms]);

  const handleCreateParamChange = useCallback((key: string, value: unknown) => {
    setCreateAlgorithmParams((prev) => ({ ...prev, [key]: value }));
  }, []);

  const handleApplyCreateIctPreset = useCallback((params: Record<string, unknown>) => {
    setCreateAlgorithmParams((prev) => ({ ...prev, ...params }));
  }, []);

  const handleSaveAlgorithm = useCallback(async () => {
    if (!selectedStrategy) return;
    setSaving(true);
    setUpdateError(null);
    const prevAlgorithm = selectedStrategy.algorithm ?? '';
    const prevParams = ((selectedStrategy.config ?? {}).algorithm_params as Record<string, unknown>) ?? {};
    try {
      await updateStrategy(selectedStrategy.id, {
        algorithm: selectedAlgorithm,
        config: { ...selectedStrategy.config, algorithm_params: algorithmParams },
      });
      setEditingAlgorithm(false);
    } catch (err) {
      setUpdateError(err instanceof Error ? err.message : 'Failed to update');
      setSelectedAlgorithm(prevAlgorithm);
      setAlgorithmParams(prevParams);
    } finally {
      setSaving(false);
    }
  }, [selectedStrategy, selectedAlgorithm, algorithmParams, updateStrategy]);

  const handleCancelEdit = useCallback(() => {
    if (selectedStrategy) {
      const config = selectedStrategy.config ?? {};
      const savedParams = (config.algorithm_params as Record<string, unknown>) ?? {};
      setSelectedAlgorithm(selectedStrategy.algorithm ?? '');
      setAlgorithmParams(
        selectedStrategy.algorithm === ICT_ALGORITHM
          ? { ...defaultParamsForAlgorithm(ICT_ALGORITHM, algorithms), ...savedParams }
          : savedParams,
      );
    }
    setEditingAlgorithm(false);
    setUpdateError(null);
  }, [selectedStrategy, algorithms]);

  const handleToggleEnabled = useCallback(async (strategy: Strategy) => {
    setTogglingEnabled(true);
    try {
      const newEnabled = !(strategy.enabled ?? (strategy.config?.enabled !== false));
      await updateStrategy(strategy.id, {
        enabled: newEnabled,
        config: { ...strategy.config, enabled: newEnabled },
      });
    } catch {
      // handled by store
    } finally {
      setTogglingEnabled(false);
    }
  }, [updateStrategy]);

  const handleSaveSettings = useCallback(async () => {
    if (!selectedStrategy) return;
    if (settingsInstruments.length === 0) {
      setSettingsError('Select at least one instrument');
      return;
    }
    setSavingSettings(true);
    setSettingsError(null);
    try {
      const tfs = Array.from(new Set([settingsEntryTf, settingsHigherTf, settingsTrendTf]));
      await updateStrategy(selectedStrategy.id, {
        config: {
          ...selectedStrategy.config,
          instruments: settingsInstruments,
          timeframes: tfs,
          entry_timeframe: settingsEntryTf,
          higher_timeframe: settingsHigherTf,
          trend_timeframe: settingsTrendTf,
          min_confidence_score: parseFloat(settingsMinConfidence) || 0.6,
          algorithm_params: algorithmParams,
          risk_settings: {
            max_risk_per_trade_pct: parseFloat(settingsMaxRisk) || 2.0,
            max_daily_loss_pct: parseFloat(settingsMaxDailyLoss) || 5.0,
            max_spread: parseFloat(settingsMaxSpread) || 50.0,
            max_slippage: parseFloat(settingsMaxSlippage) || 10.0,
            volatility_multiplier: parseFloat(settingsVolMultiplier) || 1.5,
            min_reward_risk_ratio: parseFloat(settingsMinRR) || 2.0,
          },
          exit_rules: {
            trailing_stop: {
              enabled: settingsTrailingEnabled,
              activation_pips: parseFloat(settingsTrailingActivation) || 20,
              trail_distance_pips: parseFloat(settingsTrailingDistance) || 10,
            },
            break_even: {
              enabled: settingsBreakEvenEnabled,
              activation_pips: parseFloat(settingsBreakEvenActivation) || 15,
              buffer_pips: parseFloat(settingsBreakEvenBuffer) || 2,
            },
            time_exit: {
              enabled: settingsTimeExitEnabled,
              max_duration_minutes: parseInt(settingsTimeExitMinutes, 10) || 240,
            },
            partial_close: {
              enabled: settingsPartialCloseEnabled,
              trigger_pips: parseFloat(settingsPartialTrigger) || 30,
              close_percent: parseFloat(settingsPartialPercent) || 50,
            },
          },
        },
      });
      setEditingSettings(false);
    } catch (err) {
      setSettingsError(err instanceof Error ? err.message : 'Failed to save settings');
    } finally {
      setSavingSettings(false);
    }
  }, [selectedStrategy, settingsInstruments, settingsEntryTf, settingsHigherTf, settingsTrendTf, settingsMinConfidence, algorithmParams, settingsMinRR, settingsMaxRisk, settingsMaxDailyLoss, settingsMaxSpread, settingsMaxSlippage, settingsVolMultiplier, settingsTrailingEnabled, settingsTrailingActivation, settingsTrailingDistance, settingsBreakEvenEnabled, settingsBreakEvenActivation, settingsBreakEvenBuffer, settingsTimeExitEnabled, settingsTimeExitMinutes, settingsPartialCloseEnabled, settingsPartialTrigger, settingsPartialPercent, updateStrategy]);

  const handleCancelSettings = useCallback(() => {
    if (selectedStrategy) {
      const config = selectedStrategy.config ?? {};
      const savedParams = (config.algorithm_params as Record<string, unknown>) ?? {};
      setAlgorithmParams(
        selectedStrategy.algorithm === ICT_ALGORITHM
          ? { ...defaultParamsForAlgorithm(ICT_ALGORITHM, algorithms), ...savedParams }
          : savedParams,
      );
      setSettingsInstruments((config.instruments ?? []) as string[]);
      setSettingsEntryTf(String(config.entry_timeframe ?? '30m'));
      setSettingsHigherTf(String(config.higher_timeframe ?? '1h'));
      setSettingsTrendTf(String(config.trend_timeframe ?? '4h'));
      setSettingsMinConfidence(String(config.min_confidence_score ?? '0.6'));
      const risk = (config.risk_settings ?? {}) as Record<string, unknown>;
      setSettingsMinRR(String(risk.min_reward_risk_ratio ?? '2.0'));
      setSettingsMaxRisk(String(risk.max_risk_per_trade_pct ?? '2.0'));
      setSettingsMaxDailyLoss(String(risk.max_daily_loss_pct ?? '5.0'));
      setSettingsMaxSpread(String(risk.max_spread ?? '50.0'));
      setSettingsMaxSlippage(String(risk.max_slippage ?? '10.0'));
      setSettingsVolMultiplier(String(risk.volatility_multiplier ?? '1.5'));
      // Reset exit rules
      const exitRules = (config.exit_rules ?? {}) as Record<string, unknown>;
      const trailing = (exitRules.trailing_stop ?? {}) as Record<string, unknown>;
      const breakEven = (exitRules.break_even ?? {}) as Record<string, unknown>;
      const timeExit = (exitRules.time_exit ?? {}) as Record<string, unknown>;
      const partial = (exitRules.partial_close ?? {}) as Record<string, unknown>;
      setSettingsTrailingEnabled(Boolean(trailing.enabled));
      setSettingsTrailingActivation(String(trailing.activation_pips ?? '20'));
      setSettingsTrailingDistance(String(trailing.trail_distance_pips ?? '10'));
      setSettingsBreakEvenEnabled(Boolean(breakEven.enabled));
      setSettingsBreakEvenActivation(String(breakEven.activation_pips ?? '15'));
      setSettingsBreakEvenBuffer(String(breakEven.buffer_pips ?? '2'));
      setSettingsTimeExitEnabled(Boolean(timeExit.enabled));
      setSettingsTimeExitMinutes(String(timeExit.max_duration_minutes ?? '240'));
      setSettingsPartialCloseEnabled(Boolean(partial.enabled));
      setSettingsPartialTrigger(String(partial.trigger_pips ?? '30'));
      setSettingsPartialPercent(String(partial.close_percent ?? '50'));
    }
    setEditingSettings(false);
    setSettingsError(null);
  }, [selectedStrategy, algorithms]);

  const handleCreateStrategy = useCallback(async () => {
    if (createInstruments.length === 0) {
      setCreateError('Select at least one instrument');
      return;
    }
    setCreating(true);
    setCreateError(null);
    try {
      const tfs = Array.from(new Set([createEntryTf, createHigherTf, createTrendTf]));
      await createStrategy({
        name: createName,
        algorithm: createAlgorithm,
        config: {
          instruments: createInstruments,
          timeframes: tfs,
          entry_timeframe: createEntryTf,
          higher_timeframe: createHigherTf,
          trend_timeframe: createTrendTf,
          min_confidence_score: parseFloat(createMinConfidence) || 0.6,
          enabled: createEnabled,
          mode: 'live',
          algorithm_params: createAlgorithm === ICT_ALGORITHM ? normalizeIctParams(createAlgorithmParams) : createAlgorithmParams,
          risk_settings: {
            max_risk_per_trade_pct: parseFloat(createMaxRisk) || 2.0,
            max_daily_loss_pct: parseFloat(createMaxDailyLoss) || 5.0,
            max_spread: parseFloat(createMaxSpread) || 50.0,
            max_slippage: parseFloat(createMaxSlippage) || 10.0,
            volatility_multiplier: parseFloat(createVolMultiplier) || 1.5,
            min_reward_risk_ratio: parseFloat(createMinRR) || 2.0,
          },
          exit_rules: {
            trailing_stop: {
              enabled: createTrailingEnabled,
              activation_pips: parseFloat(createTrailingActivation) || 20,
              trail_distance_pips: parseFloat(createTrailingDistance) || 10,
            },
            break_even: {
              enabled: createBreakEvenEnabled,
              activation_pips: parseFloat(createBreakEvenActivation) || 15,
              buffer_pips: parseFloat(createBreakEvenBuffer) || 2,
            },
            time_exit: {
              enabled: createTimeExitEnabled,
              max_duration_minutes: parseInt(createTimeExitMinutes, 10) || 240,
            },
            partial_close: {
              enabled: createPartialCloseEnabled,
              trigger_pips: parseFloat(createPartialTrigger) || 30,
              close_percent: parseFloat(createPartialPercent) || 50,
            },
          },
        },
      });
      setShowCreateForm(false);
      setCreateName('');
      setCreateInstruments([]);
      setCreateAlgorithmParams(defaultParamsForAlgorithm(createAlgorithm, algorithms));
      // Reset exit rules
      setCreateTrailingEnabled(false);
      setCreateTrailingActivation('20');
      setCreateTrailingDistance('10');
      setCreateBreakEvenEnabled(false);
      setCreateBreakEvenActivation('15');
      setCreateBreakEvenBuffer('2');
      setCreateTimeExitEnabled(false);
      setCreateTimeExitMinutes('240');
      setCreatePartialCloseEnabled(false);
      setCreatePartialTrigger('30');
      setCreatePartialPercent('50');
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create strategy');
    } finally {
      setCreating(false);
    }
  }, [createName, createAlgorithm, createInstruments, createEntryTf, createHigherTf, createTrendTf, createMinConfidence, createEnabled, createAlgorithmParams, createMinRR, createMaxRisk, createMaxDailyLoss, createMaxSpread, createMaxSlippage, createVolMultiplier, createTrailingEnabled, createTrailingActivation, createTrailingDistance, createBreakEvenEnabled, createBreakEvenActivation, createBreakEvenBuffer, createTimeExitEnabled, createTimeExitMinutes, createPartialCloseEnabled, createPartialTrigger, createPartialPercent, createStrategy, algorithms]);

  const handleDeleteStrategy = useCallback(async (id: string) => {
    if (!confirm('Delete this strategy? This cannot be undone.')) return;
    setDeleting(true);
    try { await deleteStrategy(id); } catch { /* store handles */ } finally { setDeleting(false); }
  }, [deleteStrategy]);

  const currentAlgInfo: AlgorithmInfo | undefined = algorithms.find((a) => a.name === selectedAlgorithm);
  const activeInstruments = instruments.filter((i) => i.isActive);
  const instrumentOptions = activeInstruments.map((i) => ({ value: i.symbol, label: `${i.displayName} (${i.symbol})` }));

  /* ── Detail panel ── */
  if (selectedStrategy) {
    const isEnabled = selectedStrategy.enabled ?? (selectedStrategy.config?.enabled !== false);
    const config = selectedStrategy.config ?? {};
    const configInstruments = (config.instruments ?? []) as string[];
    const instrumentMap = Object.fromEntries(activeInstruments.map((i) => [i.symbol, i.displayName]));
    const displayInstruments = configInstruments.length > 0
      ? configInstruments.map((sym) => instrumentMap[sym] ? `${instrumentMap[sym]} (${sym})` : sym).join(', ')
      : '—';
    const risk = (config.risk_settings ?? {}) as Record<string, unknown>;

    return (
      <div style={{ padding: 24, fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-primary)', minHeight: '100%' }}>
        {detailBacktest ? (
          <BacktestDetailView bt={detailBacktest} instrumentMap={instrumentMap} onBack={() => setDetailBacktest(null)} />
        ) : (
        <>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <button onClick={handleBack} style={backBtnStyle} aria-label="Close detail panel">← Back to Strategies</button>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <ToggleSwitch checked={isEnabled} onChange={() => handleToggleEnabled(selectedStrategy)} disabled={togglingEnabled} label={isEnabled ? 'Enabled' : 'Disabled'} />
            <button onClick={() => handleDeleteStrategy(selectedStrategy.id)} disabled={deleting} style={{
              padding: '6px 14px', fontSize: 12, fontWeight: 500,
              background: 'transparent', color: 'var(--danger, #ef4444)',
              border: '1px solid var(--danger, #ef4444)', borderRadius: 'var(--radius-sm)',
              cursor: deleting ? 'not-allowed' : 'pointer', opacity: deleting ? 0.5 : 1,
            }}>
              {deleting ? 'Deleting…' : 'Delete'}
            </button>
          </div>
        </div>

        <h2 style={{ fontSize: 18, fontWeight: 600, margin: '16px 0 20px' }}>{selectedStrategy.name}</h2>

        {/* Strategy Settings */}
        <div style={{ background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)', padding: 20, marginBottom: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <h3 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>Strategy Settings</h3>
            {!editingSettings && (
              <button onClick={() => setEditingSettings(true)} style={accentBtnStyle}>Edit Settings</button>
            )}
          </div>

          {settingsError && <div style={errorBannerStyle} role="alert"><span>{settingsError}</span></div>}

          {editingSettings ? (
            <div>
              <div style={formLabelStyle}>
                <span>Instruments</span>
                <MultiSelect options={instrumentOptions} selected={settingsInstruments} onChange={setSettingsInstruments} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginTop: 12 }}>
                <label style={formLabelStyle}>
                  Entry Timeframe
                  <select value={settingsEntryTf} onChange={(e) => setSettingsEntryTf(e.target.value)} style={inputStyle}>
                    {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
                  </select>
                </label>
                <label style={formLabelStyle}>
                  Structure Timeframe
                  <select value={settingsHigherTf} onChange={(e) => setSettingsHigherTf(e.target.value)} style={inputStyle}>
                    {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
                  </select>
                </label>
                <label style={formLabelStyle}>
                  Trend Timeframe
                  <select value={settingsTrendTf} onChange={(e) => setSettingsTrendTf(e.target.value)} style={inputStyle}>
                    {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
                  </select>
                </label>
                <label style={formLabelStyle}>
                  Min Confidence
                  <input type="number" step="0.1" min="0" max="1" value={settingsMinConfidence} onChange={(e) => setSettingsMinConfidence(e.target.value)} style={inputStyle} />
                </label>
                <label style={formLabelStyle}>
                  Min R:R Ratio
                  <input type="number" step="0.5" min="0.5" max="10" value={settingsMinRR} onChange={(e) => setSettingsMinRR(e.target.value)} style={inputStyle} />
                </label>
                <label style={formLabelStyle}>
                  Max Risk/Trade %
                  <input type="number" step="0.5" min="0.1" max="100" value={settingsMaxRisk} onChange={(e) => setSettingsMaxRisk(e.target.value)} style={inputStyle} />
                </label>
                <label style={formLabelStyle}>
                  Max Daily Loss %
                  <input type="number" step="0.5" min="0.1" max="100" value={settingsMaxDailyLoss} onChange={(e) => setSettingsMaxDailyLoss(e.target.value)} style={inputStyle} />
                </label>
                <label style={formLabelStyle}>
                  Max Spread
                  <input type="number" step="1" min="0.1" value={settingsMaxSpread} onChange={(e) => setSettingsMaxSpread(e.target.value)} style={inputStyle} />
                </label>
                <label style={formLabelStyle}>
                  Max Slippage
                  <input type="number" step="1" min="0.1" value={settingsMaxSlippage} onChange={(e) => setSettingsMaxSlippage(e.target.value)} style={inputStyle} />
                </label>
                <label style={formLabelStyle}>
                  Volatility Multiplier
                  <input type="number" step="0.1" min="0.1" value={settingsVolMultiplier} onChange={(e) => setSettingsVolMultiplier(e.target.value)} style={inputStyle} />
                </label>
              </div>

              {selectedAlgorithm === ICT_ALGORITHM && (
                <IctSelectivityEditor
                  params={algorithmParams}
                  onChange={handleParamChange}
                  onApplyPreset={handleApplyIctPreset}
                />
              )}

              {/* Exit Rules */}
              <h4 style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', margin: '16px 0 8px' }}>Exit Rules (pip values use instrument specs)</h4>
              <div style={{ display: 'grid', gap: 10 }}>
                {/* Trailing Stop */}
                <div style={{ background: 'var(--bg-primary)', borderRadius: 'var(--radius-sm)', padding: 12 }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: settingsTrailingEnabled ? 8 : 0 }}>
                    <input type="checkbox" checked={settingsTrailingEnabled} onChange={(e) => setSettingsTrailingEnabled(e.target.checked)} />
                    Trailing Stop
                  </label>
                  {settingsTrailingEnabled && (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                      <label style={formLabelStyle}>Activation (pips)<input type="number" step="1" min="1" value={settingsTrailingActivation} onChange={(e) => setSettingsTrailingActivation(e.target.value)} style={inputStyle} /></label>
                      <label style={formLabelStyle}>Trail Distance (pips)<input type="number" step="1" min="1" value={settingsTrailingDistance} onChange={(e) => setSettingsTrailingDistance(e.target.value)} style={inputStyle} /></label>
                    </div>
                  )}
                </div>
                {/* Break Even */}
                <div style={{ background: 'var(--bg-primary)', borderRadius: 'var(--radius-sm)', padding: 12 }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: settingsBreakEvenEnabled ? 8 : 0 }}>
                    <input type="checkbox" checked={settingsBreakEvenEnabled} onChange={(e) => setSettingsBreakEvenEnabled(e.target.checked)} />
                    Break-Even Stop
                  </label>
                  {settingsBreakEvenEnabled && (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                      <label style={formLabelStyle}>Activation (pips)<input type="number" step="1" min="1" value={settingsBreakEvenActivation} onChange={(e) => setSettingsBreakEvenActivation(e.target.value)} style={inputStyle} /></label>
                      <label style={formLabelStyle}>Buffer (pips)<input type="number" step="0.5" min="0" value={settingsBreakEvenBuffer} onChange={(e) => setSettingsBreakEvenBuffer(e.target.value)} style={inputStyle} /></label>
                    </div>
                  )}
                </div>
                {/* Time Exit */}
                <div style={{ background: 'var(--bg-primary)', borderRadius: 'var(--radius-sm)', padding: 12 }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: settingsTimeExitEnabled ? 8 : 0 }}>
                    <input type="checkbox" checked={settingsTimeExitEnabled} onChange={(e) => setSettingsTimeExitEnabled(e.target.checked)} />
                    Time-Based Exit
                  </label>
                  {settingsTimeExitEnabled && (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 8, maxWidth: 200 }}>
                      <label style={formLabelStyle}>Max Duration (minutes)<input type="number" step="15" min="1" value={settingsTimeExitMinutes} onChange={(e) => setSettingsTimeExitMinutes(e.target.value)} style={inputStyle} /></label>
                    </div>
                  )}
                </div>
                {/* Partial Close */}
                <div style={{ background: 'var(--bg-primary)', borderRadius: 'var(--radius-sm)', padding: 12 }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: settingsPartialCloseEnabled ? 8 : 0 }}>
                    <input type="checkbox" checked={settingsPartialCloseEnabled} onChange={(e) => setSettingsPartialCloseEnabled(e.target.checked)} />
                    Partial Close
                  </label>
                  {settingsPartialCloseEnabled && (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                      <label style={formLabelStyle}>Trigger (pips)<input type="number" step="1" min="1" value={settingsPartialTrigger} onChange={(e) => setSettingsPartialTrigger(e.target.value)} style={inputStyle} /></label>
                      <label style={formLabelStyle}>Close %<input type="number" step="5" min="1" max="100" value={settingsPartialPercent} onChange={(e) => setSettingsPartialPercent(e.target.value)} style={inputStyle} /></label>
                    </div>
                  )}
                </div>
              </div>

              <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
                <button onClick={handleSaveSettings} disabled={savingSettings} style={accentBtnStyle}>
                  {savingSettings ? '⏳ Saving…' : 'Save Settings'}
                </button>
                <button onClick={handleCancelSettings} style={backBtnStyle}>Cancel</button>
              </div>
            </div>
          ) : (
            <div>
              <DetailRow label="Instruments" value={displayInstruments} />
              <DetailRow label="Entry Timeframe" value={String(config.entry_timeframe ?? '—')} />
              <DetailRow label="Structure Timeframe" value={String(config.higher_timeframe ?? '—')} />
              <DetailRow label="Trend Timeframe" value={String(config.trend_timeframe ?? '—')} />
              <DetailRow label="Min Confidence" value={String(config.min_confidence_score ?? '—')} mono />
              {selectedAlgorithm === ICT_ALGORITHM && <IctSelectivitySummary params={algorithmParams} />}
              <h4 style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', margin: '12px 0 6px' }}>Risk Settings</h4>
              <DetailRow label="Min R:R Ratio" value={String(risk.min_reward_risk_ratio ?? '—')} mono />
              <DetailRow label="Max Risk/Trade %" value={String(risk.max_risk_per_trade_pct ?? '—')} mono />
              <DetailRow label="Max Daily Loss %" value={String(risk.max_daily_loss_pct ?? '—')} mono />
              <DetailRow label="Max Spread" value={String(risk.max_spread ?? '—')} mono />
              <DetailRow label="Max Slippage" value={String(risk.max_slippage ?? '—')} mono />
              <DetailRow label="Volatility Multiplier" value={String(risk.volatility_multiplier ?? '—')} mono />
              {(() => {
                const exitRules = (config.exit_rules ?? {}) as Record<string, unknown>;
                const trailing = (exitRules.trailing_stop ?? {}) as Record<string, unknown>;
                const breakEven = (exitRules.break_even ?? {}) as Record<string, unknown>;
                const timeExit = (exitRules.time_exit ?? {}) as Record<string, unknown>;
                const partial = (exitRules.partial_close ?? {}) as Record<string, unknown>;
                const anyEnabled = Boolean(trailing.enabled) || Boolean(breakEven.enabled) || Boolean(timeExit.enabled) || Boolean(partial.enabled);
                return (
                  <>
                    <h4 style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', margin: '12px 0 6px' }}>Exit Rules</h4>
                    {!anyEnabled && <div style={{ fontSize: 12, color: 'var(--text-secondary)', fontStyle: 'italic' }}>No exit rules enabled (broker SL/TP only)</div>}
                    {Boolean(trailing.enabled) && <DetailRow label="Trailing Stop" value={`Activate at ${trailing.activation_pips} pips, trail ${trailing.trail_distance_pips} pips`} mono />}
                    {Boolean(breakEven.enabled) && <DetailRow label="Break-Even" value={`Activate at ${breakEven.activation_pips} pips, buffer ${breakEven.buffer_pips} pips`} mono />}
                    {Boolean(timeExit.enabled) && <DetailRow label="Time Exit" value={`Close after ${timeExit.max_duration_minutes} minutes`} mono />}
                    {Boolean(partial.enabled) && <DetailRow label="Partial Close" value={`${partial.close_percent}% at ${partial.trigger_pips} pips profit`} mono />}
                  </>
                );
              })()}
            </div>
          )}
        </div>

        {/* Algorithm Settings */}
        <div style={{ background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)', padding: 20, marginBottom: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <h3 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>Algorithm Settings</h3>
            {!editingAlgorithm && (
              <button onClick={() => setEditingAlgorithm(true)} style={accentBtnStyle} disabled={algorithms.length === 0}>Edit Algorithm</button>
            )}
          </div>

          {updateError && <div style={errorBannerStyle} role="alert"><span>{updateError}</span></div>}

          {editingAlgorithm ? (
            <div>
              <label style={formLabelStyle}>
                Algorithm
                <select value={selectedAlgorithm} onChange={(e) => handleAlgorithmChange(e.target.value)} style={inputStyle} disabled={algorithms.length === 0}>
                  {algorithms.length === 0 && <option value="">No algorithms available</option>}
                  {algorithms.map((alg) => <option key={alg.name} value={alg.name}>{formatAlgorithmName(alg.name)}</option>)}
                </select>
              </label>
              {currentAlgInfo?.description && (
                <div style={{ fontSize: 11, color: 'var(--text-secondary)', margin: '8px 0 12px', fontStyle: 'italic' }}>{currentAlgInfo.description}</div>
              )}
              {(() => {
                const props = currentAlgInfo?.param_schema?.properties;
                if (!props || typeof props !== 'object') return null;
                const entries = Object.entries(props as Record<string, Record<string, unknown>>);
                if (entries.length === 0) return null;
                return (
                  <div style={{ marginTop: 12 }}>
                    <h4 style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8 }}>Parameters</h4>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                      {entries.map(([key, schema]) => {
                        const paramType = schema.type as string;
                        const value = algorithmParams[key] ?? currentAlgInfo.default_params[key] ?? '';
                        return (
                          <label key={key} style={formLabelStyle}>
                            <span>{key.replace(/_/g, ' ')}{schema.description ? ` — ${String(schema.description)}` : ''}</span>
                            <input
                              type={paramType === 'integer' || paramType === 'number' ? 'number' : 'text'}
                              value={String(value)}
                              onChange={(e) => {
                                const raw = e.target.value;
                                const parsed = paramType === 'integer' ? parseInt(raw, 10) : paramType === 'number' ? parseFloat(raw) : raw;
                                handleParamChange(key, isNaN(parsed as number) ? raw : parsed);
                              }}
                              min={schema.minimum != null ? String(schema.minimum) : undefined}
                              max={schema.maximum != null ? String(schema.maximum) : undefined}
                              step={paramType === 'integer' ? '1' : 'any'}
                              style={inputStyle}
                            />
                          </label>
                        );
                      })}
                    </div>
                  </div>
                );
              })()}
              <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
                <button onClick={handleSaveAlgorithm} disabled={saving} style={accentBtnStyle}>{saving ? '⏳ Saving…' : 'Save'}</button>
                <button onClick={handleCancelEdit} style={backBtnStyle}>Cancel</button>
              </div>
            </div>
          ) : (
            <div>
              <DetailRow label="Algorithm" value={selectedAlgorithm ? formatAlgorithmName(selectedAlgorithm) : '—'} />
              {Object.keys(algorithmParams).length > 0 && (
                <>
                  <h4 style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', margin: '12px 0 6px' }}>Parameters</h4>
                  {Object.entries(algorithmParams).map(([key, val]) => (
                    <DetailRow key={key} label={key.replace(/_/g, ' ')} value={String(val)} mono />
                  ))}
                </>
              )}
            </div>
          )}
        </div>

        {/* Backtest section */}
        <div style={{ background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)', padding: 20, marginBottom: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <h3 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>Backtest</h3>
            {!showBacktestForm && (
              <button onClick={() => setShowBacktestForm(true)} style={accentBtnStyle} disabled={backtestLoading}>Run Backtest</button>
            )}
          </div>

          {showBacktestForm && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginBottom: 12 }}>
                <label style={formLabelStyle}>
                  Instrument
                  {instrumentsLoading ? (
                    <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Loading…</span>
                  ) : instrumentsError ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ fontSize: 11, color: 'var(--danger)' }}>{instrumentsError}</span>
                      <button onClick={() => fetchInstruments()} style={{ fontSize: 10, color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline' }}>Retry</button>
                    </div>
                  ) : (
                    <select value={btInstrument} onChange={(e) => setBtInstrument(e.target.value)} style={inputStyle}>
                      {instruments.map((inst) => <option key={inst.id} value={inst.symbol}>{inst.displayName} ({inst.symbol})</option>)}
                    </select>
                  )}
                </label>
                <label style={formLabelStyle}>
                  Start Date
                  <input type="date" value={btStartDate} onChange={(e) => setBtStartDate(e.target.value)} style={inputStyle} />
                </label>
                <label style={formLabelStyle}>
                  End Date
                  <input type="date" value={btEndDate} onChange={(e) => setBtEndDate(e.target.value)} style={inputStyle} />
                </label>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button onClick={handleRunBacktest} disabled={backtestLoading} style={accentBtnStyle}>{backtestLoading ? '⏳ Running…' : 'Submit Backtest'}</button>
                <button onClick={() => setShowBacktestForm(false)} style={backBtnStyle}>Cancel</button>
              </div>
            </div>
          )}

          {backtestError && <div style={errorBannerStyle} role="alert"><span>{backtestError}</span></div>}

          {lastBacktestResult && (
            <div style={{ marginTop: 12 }}>
              <h4 style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 10 }}>Latest Result</h4>
              {(lastBacktestResult.status === 'pending' || lastBacktestResult.status === 'running') && (
                <div style={{ fontSize: 13, color: 'var(--text-secondary)', padding: 8 }}>⏳ Backtest {lastBacktestResult.status}…</div>
              )}
              {lastBacktestResult.status === 'failed' && (
                <div style={errorBannerStyle} role="alert"><span>❌ {lastBacktestResult.error_message ?? lastBacktestResult.errorMessage ?? 'Unknown error'}</span></div>
              )}
              {lastBacktestResult.status === 'completed' && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 10 }}>
                  <MetricCard label="Win Rate" value={formatPct(lastBacktestResult.win_rate ?? lastBacktestResult.winRate ?? 0)} />
                  <MetricCard label="Max Drawdown" value={formatPct(lastBacktestResult.max_drawdown ?? lastBacktestResult.maxDrawdown ?? 0)} />
                  <MetricCard label="Sharpe Ratio" value={formatNum(lastBacktestResult.sharpe_ratio ?? lastBacktestResult.sharpeRatio ?? 0)} />
                  <MetricCard label="Profit Factor" value={formatNum(lastBacktestResult.profit_factor ?? lastBacktestResult.profitFactor ?? 0)} />
                  <MetricCard label="Expectancy" value={formatNum(lastBacktestResult.expectancy ?? 0)} />
                  <MetricCard label="Total Trades" value={String(lastBacktestResult.total_trades ?? lastBacktestResult.totalTrades ?? 0)} />
                  <MetricCard label="Gross Profit" value={formatNum(lastBacktestResult.gross_profit ?? lastBacktestResult.grossProfit ?? 0)} />
                  <MetricCard label="Gross Loss" value={formatNum(lastBacktestResult.gross_loss ?? lastBacktestResult.grossLoss ?? 0)} />
                  <MetricCard label="Net Profit" value={formatNum(lastBacktestResult.net_profit ?? lastBacktestResult.netProfit ?? 0)} />
                </div>
              )}
            </div>
          )}
        </div>

        {/* Backtest History */}
        <div style={{ background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)', padding: 20 }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, margin: '0 0 12px' }}>Backtest History</h3>
          {backtestHistoryLoading && <div style={{ fontSize: 13, color: 'var(--text-secondary)', padding: 8 }}>Loading history…</div>}
          {(() => {
            const historyData = (selectedStrategyId && backtestsByStrategy[selectedStrategyId]) ? backtestsByStrategy[selectedStrategyId] : backtestHistory;
            if (!backtestHistoryLoading && historyData.length === 0) {
              return <div style={{ textAlign: 'center', padding: 24, color: 'var(--text-secondary)', fontSize: 13 }}>No backtests have been run for this strategy</div>;
            }
            if (historyData.length > 0) {
              return (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                      <tr>
                        {HISTORY_HEADERS.map((h) => <th key={h.label} style={{ ...thStyle, textAlign: h.align }}>{h.label}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {historyData.map((bt) => (
                        <tr key={bt.id} style={{ borderBottom: '1px solid var(--bg-primary)', cursor: bt.status === 'completed' ? 'pointer' : 'default' }} onClick={() => bt.status === 'completed' && setDetailBacktest(bt)}>
                          <td style={tdLeft}>{fmtDate(bt.createdAt ?? bt.created_at)}</td>
                          <td style={tdLeft}>{(() => { const sym = (bt as any).config?.instrument ?? '—'; return instrumentMap[sym] ? `${instrumentMap[sym]} (${sym})` : sym; })()}</td>
                          <td style={tdLeft}>{fmtShortDate((bt as any).config?.startDate)} – {fmtShortDate((bt as any).config?.endDate)}</td>
                          <td style={tdLeft}><StatusBadge status={bt.status} /></td>
                          {bt.status === 'completed' ? (
                            <>
                              <td style={tdRight}>{String(bt.total_trades ?? bt.totalTrades ?? 0)}</td>
                              <td style={tdRight}>{formatPct(bt.win_rate ?? bt.winRate ?? 0)}</td>
                              <td style={tdRight}>{formatNum(bt.net_profit ?? bt.netProfit ?? 0)}</td>
                            </>
                          ) : bt.status === 'failed' ? (
                            <td colSpan={3} style={{ ...tdLeft, color: 'var(--danger)' }}>{bt.error_message ?? bt.errorMessage ?? 'Unknown error'}</td>
                          ) : (
                            <td colSpan={3} style={{ ...tdLeft, color: 'var(--text-secondary)' }}>⏳ {bt.status === 'pending' ? 'Waiting…' : 'Running…'}</td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              );
            }
            return null;
          })()}
        </div>
        </>
        )}
      </div>
    );
  }

  /* ── Main list view ── */
  return (
    <div style={{ padding: 24, fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-primary)', minHeight: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>Strategies</h1>
        <button onClick={() => setShowCreateForm(true)} style={{ padding: '8px 16px', fontSize: 13, fontWeight: 500, background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)', cursor: 'pointer' }}>
          + New Strategy
        </button>
      </div>

      {/* Create form */}
      {showCreateForm && (
        <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-primary)', borderRadius: 'var(--radius-sm)', padding: 20, marginBottom: 16 }}>
          <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>New Strategy</div>
          {createError && <div style={{ color: 'var(--danger, #ef4444)', fontSize: 12, marginBottom: 8 }}>{createError}</div>}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <label style={labelStyle}>
              Name
              <input value={createName} onChange={(e) => setCreateName(e.target.value)} style={inputStyle} placeholder="e.g. V75 ICT Conservative" />
            </label>
            <label style={labelStyle}>
              Algorithm
              <select value={createAlgorithm} onChange={(e) => handleCreateAlgorithmChange(e.target.value)} style={inputStyle}>
                {algorithms.map((a) => <option key={a.name} value={a.name}>{formatAlgorithmName(a.name)}</option>)}
              </select>
            </label>
          </div>
          <div style={{ marginTop: 12 }}>
            <div style={labelStyle}>
              <span>Instruments</span>
              <MultiSelect options={instrumentOptions} selected={createInstruments} onChange={setCreateInstruments} />
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 12, marginTop: 12 }}>
            <label style={labelStyle}>
              Entry Timeframe
              <select value={createEntryTf} onChange={(e) => setCreateEntryTf(e.target.value)} style={inputStyle}>
                {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
              </select>
            </label>
            <label style={labelStyle}>
              Structure Timeframe
              <select value={createHigherTf} onChange={(e) => setCreateHigherTf(e.target.value)} style={inputStyle}>
                {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
              </select>
            </label>
            <label style={labelStyle}>
              Trend Timeframe
              <select value={createTrendTf} onChange={(e) => setCreateTrendTf(e.target.value)} style={inputStyle}>
                {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
              </select>
            </label>
            <label style={labelStyle}>
              Min Confidence
              <input type="number" step="0.1" min="0" max="1" value={createMinConfidence} onChange={(e) => setCreateMinConfidence(e.target.value)} style={inputStyle} />
            </label>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginTop: 12 }}>
            <label style={labelStyle}>
              Min R:R Ratio
              <input type="number" step="0.5" min="0.5" max="10" value={createMinRR} onChange={(e) => setCreateMinRR(e.target.value)} style={inputStyle} />
            </label>
            <label style={labelStyle}>
              Max Risk/Trade %
              <input type="number" step="0.5" min="0.1" max="100" value={createMaxRisk} onChange={(e) => setCreateMaxRisk(e.target.value)} style={inputStyle} />
            </label>
            <label style={labelStyle}>
              Max Daily Loss %
              <input type="number" step="0.5" min="0.1" max="100" value={createMaxDailyLoss} onChange={(e) => setCreateMaxDailyLoss(e.target.value)} style={inputStyle} />
            </label>
            <label style={labelStyle}>
              Max Spread
              <input type="number" step="1" min="0.1" value={createMaxSpread} onChange={(e) => setCreateMaxSpread(e.target.value)} style={inputStyle} />
            </label>
            <label style={labelStyle}>
              Max Slippage
              <input type="number" step="1" min="0.1" value={createMaxSlippage} onChange={(e) => setCreateMaxSlippage(e.target.value)} style={inputStyle} />
            </label>
            <label style={labelStyle}>
              Volatility Multiplier
              <input type="number" step="0.1" min="0.1" value={createVolMultiplier} onChange={(e) => setCreateVolMultiplier(e.target.value)} style={inputStyle} />
            </label>
          </div>
          {createAlgorithm === ICT_ALGORITHM && (
            <IctSelectivityEditor
              params={createAlgorithmParams}
              onChange={handleCreateParamChange}
              onApplyPreset={handleApplyCreateIctPreset}
            />
          )}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 12 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
              <input type="checkbox" checked={createEnabled} onChange={(e) => setCreateEnabled(e.target.checked)} />
              Enabled
            </label>
          </div>

          {/* Exit Rules */}
          <div style={{ marginTop: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8 }}>Exit Rules (pip values use instrument specs)</div>
            <div style={{ display: 'grid', gap: 8 }}>
              <div style={{ background: 'var(--bg-primary)', borderRadius: 'var(--radius-sm)', padding: 10 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: createTrailingEnabled ? 8 : 0 }}>
                  <input type="checkbox" checked={createTrailingEnabled} onChange={(e) => setCreateTrailingEnabled(e.target.checked)} />
                  Trailing Stop
                </label>
                {createTrailingEnabled && (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                    <label style={labelStyle}>Activation (pips)<input type="number" step="1" min="1" value={createTrailingActivation} onChange={(e) => setCreateTrailingActivation(e.target.value)} style={inputStyle} /></label>
                    <label style={labelStyle}>Trail Distance (pips)<input type="number" step="1" min="1" value={createTrailingDistance} onChange={(e) => setCreateTrailingDistance(e.target.value)} style={inputStyle} /></label>
                  </div>
                )}
              </div>
              <div style={{ background: 'var(--bg-primary)', borderRadius: 'var(--radius-sm)', padding: 10 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: createBreakEvenEnabled ? 8 : 0 }}>
                  <input type="checkbox" checked={createBreakEvenEnabled} onChange={(e) => setCreateBreakEvenEnabled(e.target.checked)} />
                  Break-Even Stop
                </label>
                {createBreakEvenEnabled && (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                    <label style={labelStyle}>Activation (pips)<input type="number" step="1" min="1" value={createBreakEvenActivation} onChange={(e) => setCreateBreakEvenActivation(e.target.value)} style={inputStyle} /></label>
                    <label style={labelStyle}>Buffer (pips)<input type="number" step="0.5" min="0" value={createBreakEvenBuffer} onChange={(e) => setCreateBreakEvenBuffer(e.target.value)} style={inputStyle} /></label>
                  </div>
                )}
              </div>
              <div style={{ background: 'var(--bg-primary)', borderRadius: 'var(--radius-sm)', padding: 10 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: createTimeExitEnabled ? 8 : 0 }}>
                  <input type="checkbox" checked={createTimeExitEnabled} onChange={(e) => setCreateTimeExitEnabled(e.target.checked)} />
                  Time-Based Exit
                </label>
                {createTimeExitEnabled && (
                  <div style={{ maxWidth: 200 }}>
                    <label style={labelStyle}>Max Duration (minutes)<input type="number" step="15" min="1" value={createTimeExitMinutes} onChange={(e) => setCreateTimeExitMinutes(e.target.value)} style={inputStyle} /></label>
                  </div>
                )}
              </div>
              <div style={{ background: 'var(--bg-primary)', borderRadius: 'var(--radius-sm)', padding: 10 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: createPartialCloseEnabled ? 8 : 0 }}>
                  <input type="checkbox" checked={createPartialCloseEnabled} onChange={(e) => setCreatePartialCloseEnabled(e.target.checked)} />
                  Partial Close
                </label>
                {createPartialCloseEnabled && (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                    <label style={labelStyle}>Trigger (pips)<input type="number" step="1" min="1" value={createPartialTrigger} onChange={(e) => setCreatePartialTrigger(e.target.value)} style={inputStyle} /></label>
                    <label style={labelStyle}>Close %<input type="number" step="5" min="1" max="100" value={createPartialPercent} onChange={(e) => setCreatePartialPercent(e.target.value)} style={inputStyle} /></label>
                  </div>
                )}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
            <button onClick={handleCreateStrategy} disabled={creating || !createName.trim()} style={{
              padding: '8px 20px', fontSize: 13, fontWeight: 500,
              background: 'var(--accent)', color: '#fff', border: 'none',
              borderRadius: 'var(--radius-sm)', cursor: creating ? 'not-allowed' : 'pointer',
              opacity: creating || !createName.trim() ? 0.5 : 1,
            }}>
              {creating ? 'Creating…' : 'Create'}
            </button>
            <button onClick={() => { setShowCreateForm(false); setCreateError(null); }} style={{
              padding: '8px 20px', fontSize: 13, fontWeight: 500,
              background: 'transparent', color: 'var(--text-secondary)',
              border: '1px solid var(--border-primary)', borderRadius: 'var(--radius-sm)', cursor: 'pointer',
            }}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {error && <div style={errorBannerStyle} role="alert"><span>{error}</span><button onClick={fetchStrategies} style={retryBtnStyle}>Retry</button></div>}
      {loading && strategies.length === 0 && <div style={{ fontSize: 13, color: 'var(--text-secondary)', padding: 16 }}>Loading strategies…</div>}
      {!loading && !error && strategies.length === 0 && (
        <div style={{ textAlign: 'center', padding: 48, color: 'var(--text-secondary)', fontSize: 14, background: 'var(--bg-surface)', borderRadius: 'var(--radius-sm)' }}>
          No strategies configured
        </div>
      )}

      {strategies.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12 }}>
          {strategies.map((strategy) => (
            <StrategyCard
              key={strategy.id}
              strategy={strategy}
              algorithms={algorithms}
              onClick={() => handleSelectStrategy(strategy.id)}
              onToggle={() => handleToggleEnabled(strategy)}
              togglingEnabled={togglingEnabled}
            />
          ))}
        </div>
      )}
    </div>
  );
};

/* ── Status Badge ── */

const statusConfig: Record<string, { icon: string; color: string }> = {
  pending: { icon: '⏳', color: 'var(--text-secondary)' },
  running: { icon: '⏳', color: 'var(--accent)' },
  completed: { icon: '✅', color: 'var(--success, #22c55e)' },
  failed: { icon: '❌', color: 'var(--danger)' },
};

const StatusBadge: FC<{ status: string }> = ({ status }) => {
  const cfg = statusConfig[status] ?? statusConfig.pending;
  const label = status.charAt(0).toUpperCase() + status.slice(1);
  return <span style={{ fontSize: 12, color: cfg.color, fontWeight: 500 }}>{cfg.icon} {label}</span>;
};

/* ── Strategy Card ── */

const StrategyCard: FC<{
  strategy: Strategy;
  algorithms: AlgorithmInfo[];
  onClick: () => void;
  onToggle: () => void;
  togglingEnabled: boolean;
}> = ({ strategy, algorithms, onClick, onToggle, togglingEnabled }) => {
  const isEnabled = strategy.enabled ?? (strategy.config?.enabled !== false);
  const configInstruments = (strategy.config?.instruments ?? []) as string[];
  const displayInst = configInstruments.length > 0 ? configInstruments.join(', ') : '—';

  return (
    <div style={{ ...cardStyle, opacity: isEnabled ? 1 : 0.6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div onClick={onClick} style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', cursor: 'pointer', flex: 1 }} role="button" tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter') onClick(); }}>
          {strategy.name}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {strategy.algorithm && algorithms.length > 0 && (
            <span style={algBadgeStyle}>{formatAlgorithmName(strategy.algorithm)}</span>
          )}
          <ToggleSwitch checked={isEnabled} onChange={() => onToggle()} disabled={togglingEnabled} />
        </div>
      </div>
      <div onClick={onClick} style={{ cursor: 'pointer' }} role="button" tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter') onClick(); }}>
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0' }}>
            <span>instruments</span>
            <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>{displayInst}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0' }}>
            <span>entry_timeframe</span>
            <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>{String(strategy.config?.entry_timeframe ?? '—')}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0' }}>
            <span>structure_timeframe</span>
            <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>{String(strategy.config?.higher_timeframe ?? '—')}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0' }}>
            <span>trend_timeframe</span>
            <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>{String(strategy.config?.trend_timeframe ?? '—')}</span>
          </div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-muted)' }}>
          <span>Created {fmtDate(strategy.createdAt ?? strategy.created_at)}</span>
          <span>{isEnabled ? '● Active' : '○ Disabled'}</span>
        </div>
      </div>
    </div>
  );
};

/* ── Table config ── */

const HISTORY_HEADERS = [
  { label: 'Run Date', align: 'left' as const },
  { label: 'Instrument', align: 'left' as const },
  { label: 'Period', align: 'left' as const },
  { label: 'Status', align: 'left' as const },
  { label: 'Trades', align: 'right' as const },
  { label: 'Win Rate', align: 'right' as const },
  { label: 'Net Profit', align: 'right' as const },
];

/* ── Styles ── */

const thStyle: React.CSSProperties = {
  padding: '10px 12px', fontFamily: 'var(--font-sans)', fontWeight: 500,
  color: 'var(--text-secondary)', borderBottom: '1px solid var(--text-secondary)',
  whiteSpace: 'nowrap', fontSize: 11,
};

const tdBase: React.CSSProperties = { padding: '8px 12px', fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' };
const tdLeft: React.CSSProperties = { ...tdBase, textAlign: 'left' };
const tdRight: React.CSSProperties = { ...tdBase, textAlign: 'right' };

const backBtnStyle: React.CSSProperties = {
  background: 'none', border: '1px solid var(--border-primary)', borderRadius: 'var(--radius-sm)',
  color: 'var(--text-secondary)', padding: '6px 12px', fontSize: 12, cursor: 'pointer',
};

const errorBannerStyle: React.CSSProperties = {
  background: 'var(--danger-bg)', border: '1px solid var(--danger)', borderRadius: 'var(--radius-md)',
  padding: '8px 12px', marginBottom: 12, display: 'flex', justifyContent: 'space-between',
  alignItems: 'center', fontSize: 12, color: 'var(--danger)',
};

const retryBtnStyle: React.CSSProperties = {
  background: 'none', border: '1px solid var(--danger)', borderRadius: 'var(--radius-sm)',
  color: 'var(--danger)', padding: '4px 10px', fontSize: 11, cursor: 'pointer',
};

const accentBtnStyle: React.CSSProperties = {
  background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)',
  padding: '6px 14px', fontSize: 12, fontWeight: 600, cursor: 'pointer',
};

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)', padding: 16,
  border: '1px solid var(--border-primary)', transition: 'border-color 0.15s',
};

const metricCardStyle: React.CSSProperties = {
  background: 'var(--bg-primary)', borderRadius: 'var(--radius-sm)', padding: '10px 12px',
  border: '1px solid var(--border-primary)',
};

const formLabelStyle: React.CSSProperties = {
  display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-secondary)',
};

const labelStyle = formLabelStyle;

const inputStyle: React.CSSProperties = {
  background: 'var(--bg-primary)', color: 'var(--text-primary)',
  border: '1px solid var(--border-primary)', borderRadius: 'var(--radius-sm)',
  padding: '6px 8px', fontSize: 12,
};

const algBadgeStyle: React.CSSProperties = {
  display: 'inline-block', background: 'var(--accent, #3b82f6)', color: '#fff',
  fontSize: 10, fontWeight: 600, padding: '2px 8px', borderRadius: 10, whiteSpace: 'nowrap',
};

/* ── Backtest Detail View (full-page tabbed) ── */

const PAGE_SIZE = 50;

const sectionTitle: React.CSSProperties = {
  fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', margin: '0 0 8px',
  textTransform: 'uppercase', letterSpacing: '0.5px',
};

const detailGrid: React.CSSProperties = {
  display: 'grid', gridTemplateColumns: '1fr 1fr', columnGap: 24, rowGap: 8,
};

const tradeThStyle: React.CSSProperties = {
  padding: '8px 10px', fontWeight: 500, fontSize: 11, color: 'var(--text-muted)',
  borderBottom: '1px solid var(--border-primary)', whiteSpace: 'nowrap', cursor: 'pointer',
  userSelect: 'none',
};

const tradeTdStyle: React.CSSProperties = { padding: '6px 10px', fontSize: 12 };
const tradeTdMono: React.CSSProperties = { ...tradeTdStyle, fontFamily: 'var(--font-mono)' };

const pageBtnStyle: React.CSSProperties = {
  padding: '5px 14px', fontSize: 12, fontWeight: 500, borderRadius: 'var(--radius-sm)',
  border: '1px solid var(--border-primary)', background: 'var(--bg-surface)',
  color: 'var(--text-primary)', cursor: 'pointer',
};

const tabStyle = (active: boolean): React.CSSProperties => ({
  padding: '8px 20px', fontSize: 13, fontWeight: active ? 600 : 400, cursor: 'pointer',
  background: 'none', border: 'none', borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
  color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
});

type SortField = 'tradeIndex' | 'positionSize' | 'profitLoss' | 'rewardRisk' | 'entryTime';
type SortDir = 'asc' | 'desc';
type DirFilter = 'ALL' | 'BUY' | 'SELL';

const BacktestDetailView: FC<{ bt: BacktestResult; instrumentMap: Record<string, string>; onBack: () => void }> = ({ bt, instrumentMap, onBack }) => {
  const [tab, setTab] = useState<'overview' | 'trades'>('overview');
  const [trades, setTrades] = useState<import('../types/api').BacktestTrade[]>([]);
  const [tradesTotal, setTradesTotal] = useState(0);
  const [tradesPage, setTradesPage] = useState(0);
  const [tradesLoading, setTradesLoading] = useState(false);
  const [tradesError, setTradesError] = useState<string | null>(null);
  const [sortField, setSortField] = useState<SortField>('tradeIndex');
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [dirFilter, setDirFilter] = useState<DirFilter>('ALL');
  const [chartTrade, setChartTrade] = useState<BacktestTrade | null>(null);

  const fetchPage = useCallback(async (page: number) => {
    setTradesLoading(true);
    setTradesError(null);
    try {
      const res = await apiClient.strategies.getBacktestTrades(bt.id, page * PAGE_SIZE, PAGE_SIZE);
      setTrades(res.items ?? []);
      setTradesTotal(res.total ?? 0);
      setTradesPage(page);
    } catch (err) {
      setTradesError(err instanceof Error ? err.message : 'Failed to load trades');
    } finally {
      setTradesLoading(false);
    }
  }, [bt.id]);

  useEffect(() => {
    if (tab === 'trades' && (!trades || trades.length === 0) && !tradesLoading) fetchPage(0);
  }, [tab]);

  const handleSort = (field: SortField) => {
    if (sortField === field) setSortDir((d) => d === 'asc' ? 'desc' : 'asc');
    else { setSortField(field); setSortDir('asc'); }
  };

  const sortArrow = (field: SortField) => sortField === field ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '';

  const safeT = trades ?? [];
  const filtered = dirFilter === 'ALL' ? safeT : safeT.filter((t) => t.direction === dirFilter);
  const sorted = [...filtered].sort((a, b) => {
    let cmp = 0;
    if (sortField === 'tradeIndex') cmp = a.tradeIndex - b.tradeIndex;
    else if (sortField === 'positionSize') cmp = Number(a.positionSize) - Number(b.positionSize);
    else if (sortField === 'profitLoss') cmp = Number(a.profitLoss) - Number(b.profitLoss);
    else if (sortField === 'rewardRisk') cmp = Number(a.rewardRisk ?? 0) - Number(b.rewardRisk ?? 0);
    else if (sortField === 'entryTime') cmp = new Date(a.entryTime).getTime() - new Date(b.entryTime).getTime();
    return sortDir === 'asc' ? cmp : -cmp;
  });

  const totalPages = Math.ceil(tradesTotal / PAGE_SIZE);
  const wins = safeT.filter((t) => Number(t.profitLoss) > 0).length;
  const losses = safeT.filter((t) => Number(t.profitLoss) <= 0).length;

  const cfg = (bt as any).config ?? {};
  const strategySnapshot = cfg.strategySnapshot ?? {};
  const risk = (strategySnapshot.risk_settings ?? {}) as Record<string, unknown>;
  const algParams = (strategySnapshot.algorithm_params ?? {}) as Record<string, unknown>;
  const configInstruments = (strategySnapshot.instruments ?? []) as string[];
  const sym = cfg.instrument ?? '—';
  const instrumentDisplay = instrumentMap[sym] ? `${instrumentMap[sym]} (${sym})` : sym;

  return (
    <div>
      <button onClick={onBack} style={backBtnStyle} aria-label="Back to strategy">← Back to Strategy</button>
      <h2 style={{ fontSize: 17, fontWeight: 600, margin: '12px 0 16px' }}>Backtest Details</h2>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid var(--border-primary)', marginBottom: 20 }}>
        <button style={tabStyle(tab === 'overview')} onClick={() => setTab('overview')}>Overview</button>
        <button style={tabStyle(tab === 'trades')} onClick={() => setTab('trades')}>Trades ({bt.total_trades ?? bt.totalTrades ?? 0})</button>
      </div>

      {/* ── Overview Tab ── */}
      {tab === 'overview' && (
        <>
          {/* Config */}
          <div style={{ background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)', padding: 20, marginBottom: 16 }}>
            <h4 style={sectionTitle}>Configuration</h4>
            <div style={detailGrid}>
              <DetailRow label="Instrument" value={instrumentDisplay} mono />
              <DetailRow label="Entry Timeframe" value={String(strategySnapshot.entry_timeframe ?? cfg.timeframe ?? '—')} mono />
              <DetailRow label="Structure Timeframe" value={String(strategySnapshot.higher_timeframe ?? '—')} mono />
              <DetailRow label="Trend Timeframe" value={String(strategySnapshot.trend_timeframe ?? '—')} mono />
              <DetailRow label="Start Date" value={cfg.startDate ? fmtFullDate(cfg.startDate) : '—'} />
              <DetailRow label="End Date" value={cfg.endDate ? fmtFullDate(cfg.endDate) : '—'} />
              <DetailRow label="Run Date" value={fmtFullDate(bt.createdAt ?? bt.created_at)} />
              <DetailRow label="Status"><StatusBadge status={bt.status} /></DetailRow>
            </div>
          </div>

          {/* Performance */}
          {bt.status === 'completed' && (
            <div style={{ background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)', padding: 20, marginBottom: 16 }}>
              <h4 style={sectionTitle}>Performance</h4>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 8 }}>
                <MetricCard label="Total Trades" value={String(bt.total_trades ?? bt.totalTrades ?? 0)} />
                <MetricCard label="Winning" value={String(bt.winning_trades ?? bt.winningTrades ?? 0)} />
                <MetricCard label="Losing" value={String(bt.losing_trades ?? bt.losingTrades ?? 0)} />
                <MetricCard label="Win Rate" value={formatPct(bt.win_rate ?? bt.winRate ?? 0)} />
                <MetricCard label="Profit Factor" value={formatNum(bt.profit_factor ?? bt.profitFactor ?? 0)} />
                <MetricCard label="Sharpe Ratio" value={formatNum(bt.sharpe_ratio ?? bt.sharpeRatio ?? 0)} />
                <MetricCard label="Max Drawdown" value={formatPct(bt.max_drawdown ?? bt.maxDrawdown ?? 0)} />
                <MetricCard label="Expectancy" value={formatNum(bt.expectancy ?? 0)} />
                <MetricCard label="Avg RR" value={formatNum(bt.average_rr ?? bt.averageRr ?? 0)} />
              </div>
            </div>
          )}

          {/* PnL */}
          {bt.status === 'completed' && (
            <div style={{ background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)', padding: 20, marginBottom: 16 }}>
              <h4 style={sectionTitle}>Profit & Loss</h4>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
                <MetricCard label="Gross Profit" value={formatNum(bt.gross_profit ?? bt.grossProfit ?? 0)} />
                <MetricCard label="Gross Loss" value={formatNum(bt.gross_loss ?? bt.grossLoss ?? 0)} />
                <MetricCard label="Net Profit" value={formatNum(bt.net_profit ?? bt.netProfit ?? 0)} />
              </div>
            </div>
          )}

          {/* Strategy Snapshot */}
          {Object.keys(strategySnapshot).length > 0 && (
            <div style={{ background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)', padding: 20, marginBottom: 16 }}>
              <h4 style={sectionTitle}>Strategy Snapshot</h4>
              <div style={detailGrid}>
                <DetailRow label="Algorithm" value={String(strategySnapshot.algorithm ?? '—')} />
                <DetailRow label="Instruments" value={configInstruments.length > 0 ? configInstruments.join(', ') : '—'} />
                <DetailRow label="Min Confidence" value={String(strategySnapshot.min_confidence_score ?? '—')} mono />
                <DetailRow label="Mode" value={String(strategySnapshot.mode ?? '—')} />
              </div>
              {Object.keys(algParams).length > 0 && (
                <>
                  <h4 style={{ ...sectionTitle, marginTop: 14 }}>Algorithm Parameters</h4>
                  <div style={detailGrid}>
                    {Object.entries(algParams).map(([key, val]) => (
                      <DetailRow key={key} label={key.replace(/_/g, ' ')} value={String(val)} mono />
                    ))}
                  </div>
                </>
              )}
              {Object.keys(risk).length > 0 && (
                <>
                  <h4 style={{ ...sectionTitle, marginTop: 14 }}>Risk Settings</h4>
                  <div style={detailGrid}>
                    {Object.entries(risk).map(([key, val]) => (
                      <DetailRow key={key} label={key.replace(/_/g, ' ')} value={String(val)} mono />
                    ))}
                  </div>
                </>
              )}
            </div>
          )}

          {bt.status === 'failed' && (
            <div style={{ ...errorBannerStyle, marginTop: 0 }}>
              <span>❌ {bt.error_message ?? bt.errorMessage ?? 'Unknown error'}</span>
            </div>
          )}
        </>
      )}

      {/* ── Trades Tab ── */}
      {tab === 'trades' && (
        <div>
          {/* Summary bar + filter */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              Page {tradesPage + 1} of {totalPages || 1} · {tradesTotal} total trades
              {trades.length > 0 && <span> · <span style={{ color: 'var(--success)' }}>{wins}W</span> / <span style={{ color: 'var(--danger)' }}>{losses}L</span> (this page)</span>}
            </div>
            <div style={{ display: 'flex', gap: 4 }}>
              {(['ALL', 'BUY', 'SELL'] as DirFilter[]).map((d) => (
                <button key={d} onClick={() => setDirFilter(d)} style={{
                  ...pageBtnStyle, fontSize: 11, padding: '3px 10px',
                  background: dirFilter === d ? 'var(--accent)' : 'var(--bg-surface)',
                  color: dirFilter === d ? '#fff' : 'var(--text-secondary)',
                  border: dirFilter === d ? '1px solid var(--accent)' : '1px solid var(--border-primary)',
                }}>{d}</button>
              ))}
            </div>
          </div>

          {tradesError && <div style={errorBannerStyle}><span>{tradesError}</span></div>}
          {tradesLoading && <div style={{ fontSize: 13, color: 'var(--text-secondary)', padding: 16 }}>Loading trades…</div>}

          {!tradesLoading && sorted.length > 0 && (
            <div style={{ overflowX: 'auto', background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-primary)' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={tradeThStyle} onClick={() => handleSort('tradeIndex')}>#{ sortArrow('tradeIndex')}</th>
                    <th style={{ ...tradeThStyle, cursor: 'default' }}>Dir</th>
                    <th style={{ ...tradeThStyle, cursor: 'default' }}>Entry</th>
                    <th style={{ ...tradeThStyle, cursor: 'default' }}>Exit</th>
                    <th style={{ ...tradeThStyle, cursor: 'default' }}>SL</th>
                    <th style={{ ...tradeThStyle, cursor: 'default' }}>TP</th>
                    <th style={tradeThStyle} onClick={() => handleSort('positionSize')}>Size{sortArrow('positionSize')}</th>
                    <th style={tradeThStyle} onClick={() => handleSort('profitLoss')}>P/L{sortArrow('profitLoss')}</th>
                    <th style={tradeThStyle} onClick={() => handleSort('rewardRisk')}>RR{sortArrow('rewardRisk')}</th>
                    <th style={{ ...tradeThStyle, cursor: 'default' }}>Bal Before</th>
                    <th style={{ ...tradeThStyle, cursor: 'default' }}>Bal After</th>
                    <th style={tradeThStyle} onClick={() => handleSort('entryTime')}>Entry Time{sortArrow('entryTime')}</th>
                    <th style={{ ...tradeThStyle, cursor: 'default' }}>Exit Time</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((t) => {
                    const pnl = Number(t.profitLoss);
                    const rr = t.rewardRisk != null ? Number(t.rewardRisk) : null;
                    return (
                      <tr key={t.id} style={{ borderTop: '1px solid var(--border-primary)', cursor: 'pointer' }} onClick={() => setChartTrade(t)}>
                        <td style={tradeTdMono}>{t.tradeIndex + 1}</td>
                        <td style={{ ...tradeTdStyle, color: t.direction === 'BUY' ? 'var(--success)' : 'var(--danger)', fontWeight: 600 }}>{t.direction}</td>
                        <td style={tradeTdMono}>{Number(t.entryPrice).toFixed(2)}</td>
                        <td style={tradeTdMono}>{Number(t.exitPrice).toFixed(2)}</td>
                        <td style={tradeTdMono}>{t.stopLoss ? Number(t.stopLoss).toFixed(2) : '—'}</td>
                        <td style={tradeTdMono}>{t.takeProfit ? Number(t.takeProfit).toFixed(2) : '—'}</td>
                        <td style={tradeTdMono}>{Number(t.positionSize).toFixed(2)}</td>
                        <td style={{ ...tradeTdMono, color: pnl > 0 ? 'var(--success)' : pnl < 0 ? 'var(--danger)' : 'var(--text-secondary)' }}>
                          {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
                        </td>
                        <td style={{ ...tradeTdMono, color: rr != null ? (rr > 0 ? 'var(--success)' : 'var(--danger)') : 'var(--text-secondary)' }}>
                          {rr != null ? rr.toFixed(2) : '—'}
                        </td>
                        <td style={tradeTdMono}>{(t as any).balanceBefore ? `$${Number((t as any).balanceBefore).toFixed(2)}` : '—'}</td>
                        <td style={tradeTdMono}>{(t as any).balanceAfter ? `$${Number((t as any).balanceAfter).toFixed(2)}` : '—'}</td>
                        <td style={{ ...tradeTdStyle, fontSize: 11 }}>{fmtDate(t.entryTime)}</td>
                        <td style={{ ...tradeTdStyle, fontSize: 11 }}>{fmtDate(t.exitTime)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {!tradesLoading && sorted.length === 0 && !tradesError && (
            <div style={{ textAlign: 'center', padding: 24, color: 'var(--text-secondary)', fontSize: 13 }}>
              {dirFilter !== 'ALL' ? `No ${dirFilter} trades on this page` : 'No trades recorded'}
            </div>
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 12, marginTop: 16 }}>
              <button onClick={() => fetchPage(tradesPage - 1)} disabled={tradesPage === 0 || tradesLoading} style={{ ...pageBtnStyle, opacity: tradesPage === 0 ? 0.4 : 1 }}>← Prev</button>
              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Page {tradesPage + 1} / {totalPages}</span>
              <button onClick={() => fetchPage(tradesPage + 1)} disabled={tradesPage >= totalPages - 1 || tradesLoading} style={{ ...pageBtnStyle, opacity: tradesPage >= totalPages - 1 ? 0.4 : 1 }}>Next →</button>
            </div>
          )}
        </div>
      )}

      {/* Trade Chart Modal */}
      {chartTrade && (
        <TradeChartModal
          trade={chartTrade}
          instrument={sym}
          timeframe={String(strategySnapshot.entry_timeframe ?? cfg.timeframe ?? '1m')}
          onClose={() => setChartTrade(null)}
        />
      )}
    </div>
  );
};

/* ── Trade Chart Modal ── */

const CANDLE_PAD_BEFORE = 100;
const CANDLE_PAD_AFTER = 30;

function toChartTime(iso: string): Time {
  return Math.floor(new Date(iso).getTime() / 1000) as Time;
}

function padTime(iso: string, minutes: number): string {
  const d = new Date(iso);
  d.setMinutes(d.getMinutes() + minutes);
  return d.toISOString();
}

function tfMinutes(tf: string): number {
  const map: Record<string, number> = { '1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240, '1d': 1440 };
  return map[tf] ?? 1;
}

const TradeChartModal: FC<{
  trade: BacktestTrade;
  instrument: string;
  timeframe: string;
  onClose: () => void;
}> = ({ trade, instrument, timeframe, onClose }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTf, setActiveTf] = useState(timeframe);
  const availableTfs = ['1m', '5m', '15m', '30m', '1h', '4h'];

  useEffect(() => {
    if (!containerRef.current) return;

    const mins = tfMinutes(activeTf);
    const startDate = padTime(trade.entryTime, -mins * CANDLE_PAD_BEFORE);
    const endDate = padTime(trade.exitTime, mins * CANDLE_PAD_AFTER);

    let cancelled = false;

    (async () => {
      try {
        const candles = await apiClient.marketData.getCandlesByRange({
          instrument,
          timeframe: activeTf,
          startDate,
          endDate,
        });

        if (cancelled || !containerRef.current) return;

        if (candles.length === 0) {
          setError('No candle data available for this time range');
          setLoading(false);
          return;
        }

        const chart = createChart(containerRef.current, {
          width: containerRef.current.clientWidth,
          height: 560,
          layout: {
            background: { type: ColorType.Solid, color: '#0d1117' },
            textColor: '#8b949e',
            fontFamily: "'JetBrains Mono', monospace",
          },
          grid: {
            vertLines: { color: '#1c2128' },
            horzLines: { color: '#1c2128' },
          },
          crosshair: {
            mode: CrosshairMode.Normal,
            vertLine: { color: '#484f58', labelBackgroundColor: '#21262d' },
            horzLine: { color: '#484f58', labelBackgroundColor: '#21262d' },
          },
          rightPriceScale: { borderColor: '#30363d' },
          timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: false },
        });

        chartRef.current = chart;

        const series = chart.addCandlestickSeries({
          upColor: '#3fb950',
          downColor: '#f85149',
          borderUpColor: '#3fb950',
          borderDownColor: '#f85149',
          wickUpColor: '#3fb950',
          wickDownColor: '#f85149',
        });

        const chartData: CandlestickData<Time>[] = candles.map((c) => ({
          time: toChartTime(c.timestamp),
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        }));

        series.setData(chartData);

        // Entry price line
        const entryPrice = Number(trade.entryPrice);
        series.createPriceLine({
          price: entryPrice,
          color: '#58a6ff',
          lineWidth: 2,
          lineStyle: 0,
          axisLabelVisible: true,
          title: `Entry ${entryPrice.toFixed(2)}`,
        });

        // Exit price line
        const exitPrice = Number(trade.exitPrice);
        const pnl = Number(trade.profitLoss);
        series.createPriceLine({
          price: exitPrice,
          color: pnl >= 0 ? '#3fb950' : '#f85149',
          lineWidth: 2,
          lineStyle: 0,
          axisLabelVisible: true,
          title: `Exit ${exitPrice.toFixed(2)}`,
        });

        // SL line (current/final SL — may have been modified by trailing)
        if (trade.stopLoss) {
          series.createPriceLine({
            price: Number(trade.stopLoss),
            color: '#f85149',
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: 'SL',
          });
        }

        // Initial SL line (original SL before trailing modified it)
        if ((trade as any).initialStopLoss && (trade as any).initialStopLoss !== trade.stopLoss) {
          series.createPriceLine({
            price: Number((trade as any).initialStopLoss),
            color: '#f8514980',
            lineWidth: 1,
            lineStyle: 3,
            axisLabelVisible: true,
            title: 'Initial SL',
          });
        }

        // TP line
        if (trade.takeProfit) {
          series.createPriceLine({
            price: Number(trade.takeProfit),
            color: '#3fb950',
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: 'TP',
          });
        }

        // Entry & exit markers on candles
        const entryTime = toChartTime(trade.entryTime);
        const exitTime = toChartTime(trade.exitTime);
        const isBuy = trade.direction === 'BUY';
        const markers: SeriesMarker<Time>[] = [
          {
            time: entryTime,
            position: isBuy ? 'belowBar' : 'aboveBar',
            color: '#58a6ff',
            shape: isBuy ? 'arrowUp' : 'arrowDown',
            text: `Entry ${entryPrice.toFixed(2)}`,
          },
          {
            time: exitTime,
            position: isBuy ? 'aboveBar' : 'belowBar',
            color: pnl >= 0 ? '#3fb950' : '#f85149',
            shape: isBuy ? 'arrowDown' : 'arrowUp',
            text: `Exit ${exitPrice.toFixed(2)}`,
          },
        ];
        markers.sort((a, b) => (a.time as number) - (b.time as number));
        series.setMarkers(markers);

        chart.timeScale().fitContent();
        setLoading(false);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load candles');
          setLoading(false);
        }
      }
    })();

    const handleResize = () => {
      if (chartRef.current && containerRef.current) {
        chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      cancelled = true;
      window.removeEventListener('resize', handleResize);
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
    };
  }, [trade, instrument, activeTf]);

  const pnl = Number(trade.profitLoss);
  const rr = trade.rewardRisk != null ? Number(trade.rewardRisk) : null;

  return (
    <div style={tradeModalOverlay} onClick={onClose} role="dialog" aria-modal="true" aria-label="Trade chart">
      <div style={tradeModalContent} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 15, fontWeight: 600 }}>Trade #{trade.tradeIndex + 1}</span>
            <span style={{ fontSize: 13, fontWeight: 600, color: trade.direction === 'BUY' ? 'var(--success)' : 'var(--danger)' }}>{trade.direction}</span>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{instrument} · {activeTf}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {/* Timeframe toggle */}
            <div style={{ display: 'flex', gap: 2, background: 'var(--bg-tertiary)', borderRadius: 6, padding: 2 }}>
              {availableTfs.map((tf) => (
                <button
                  key={tf}
                  onClick={() => { setActiveTf(tf); setLoading(true); setError(null); }}
                  style={{
                    padding: '3px 8px', fontSize: 11, fontWeight: 600, borderRadius: 4,
                    border: 'none', cursor: 'pointer',
                    background: tf === activeTf ? 'var(--accent)' : 'transparent',
                    color: tf === activeTf ? '#fff' : 'var(--text-muted)',
                    transition: 'var(--transition-fast)',
                  }}
                >
                  {tf}
                </button>
              ))}
            </div>
            <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', fontSize: 18, cursor: 'pointer', padding: '0 4px', lineHeight: 1 }} aria-label="Close">✕</button>
          </div>
        </div>

        {/* Trade info bar */}
        <div style={{ display: 'flex', gap: 16, marginBottom: 12, fontSize: 12, flexWrap: 'wrap' }}>
          <span>Entry: <span style={{ fontFamily: 'var(--font-mono)', color: '#58a6ff' }}>{Number(trade.entryPrice).toFixed(2)}</span></span>
          <span>Exit: <span style={{ fontFamily: 'var(--font-mono)', color: pnl >= 0 ? 'var(--success)' : 'var(--danger)' }}>{Number(trade.exitPrice).toFixed(2)}</span></span>
          {trade.stopLoss && <span>SL: <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--danger)' }}>{Number(trade.stopLoss).toFixed(2)}</span></span>}
          {trade.takeProfit && <span>TP: <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--success)' }}>{Number(trade.takeProfit).toFixed(2)}</span></span>}
          <span>Size: <span style={{ fontFamily: 'var(--font-mono)' }}>{Number(trade.positionSize).toFixed(2)}</span></span>
          <span>P/L: <span style={{ fontFamily: 'var(--font-mono)', color: pnl >= 0 ? 'var(--success)' : 'var(--danger)' }}>{pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}</span></span>
          {rr != null && <span>RR: <span style={{ fontFamily: 'var(--font-mono)', color: rr > 0 ? 'var(--success)' : 'var(--danger)' }}>{rr.toFixed(2)}</span></span>}
          {(trade as any).balanceBefore && <span>Bal: <span style={{ fontFamily: 'var(--font-mono)' }}>${Number((trade as any).balanceBefore).toFixed(2)} → ${Number((trade as any).balanceAfter).toFixed(2)}</span></span>}
        </div>

        {/* Chart container */}
        <div ref={containerRef} style={{ width: '100%', height: 560, borderRadius: 'var(--radius-sm)', overflow: 'hidden', background: '#0d1117' }}>
          {loading && <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-secondary)', fontSize: 13 }}>Loading chart…</div>}
          {error && <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--danger)', fontSize: 13 }}>{error}</div>}
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontSize: 11, color: 'var(--text-muted)' }}>
          <span>{fmtFullDate(trade.entryTime)} → {fmtFullDate(trade.exitTime)}</span>
          <span style={{ display: 'flex', gap: 12 }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ width: 12, height: 2, background: '#58a6ff', display: 'inline-block' }} /> Entry</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ width: 12, height: 2, background: pnl >= 0 ? '#3fb950' : '#f85149', display: 'inline-block' }} /> Exit</span>
            {trade.stopLoss && <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ width: 12, height: 2, background: '#f85149', display: 'inline-block', borderTop: '1px dashed #f85149' }} /> SL</span>}
            {trade.takeProfit && <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ width: 12, height: 2, background: '#3fb950', display: 'inline-block', borderTop: '1px dashed #3fb950' }} /> TP</span>}
          </span>
        </div>
      </div>
    </div>
  );
};

const tradeModalOverlay: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', display: 'flex',
  alignItems: 'center', justifyContent: 'center', zIndex: 1000,
};

const tradeModalContent: React.CSSProperties = {
  background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)', padding: 24,
  border: '1px solid var(--border-primary)', maxWidth: 1400, width: '95%',
};

export default StrategiesPage;
