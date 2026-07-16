import { Timeframe } from './timeframe';

export type SignalDirection = 'BUY' | 'SELL';
export type SignalMode = 'backtest' | 'forward_test' | 'live';
export type BOSType = 'bullish' | 'bearish';

/** Why a signal did or didn't become a position (from the backend, or derived
 *  client-side from mode when execution info isn't attached yet). */
export type SignalExecutionStatus = 'executed' | 'no_fill' | 'paper' | 'backtest' | 'pending';
export interface SignalExecution {
  status: SignalExecutionStatus;
  label: string;
  reason: string;
}

export interface SignalMetadata {
  bos_type: BOSType;
  liquidity_swept: boolean;
  session: string;
  spread_at_generation: number;
  volatility_ratio: number;
}

/** Normalized signal — all fields are snake_case with parsed numbers */
export interface Signal {
  id: string;
  instrument: string;
  direction: SignalDirection;
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  position_size: number;
  confidence_score: number;
  timeframe: Timeframe;
  order_block_id: string;
  strategy_id: string;
  strategy_name: string | null;
  mode: SignalMode;
  execution: SignalExecution | null;
  metadata: SignalMetadata | null;
  created_at: string;
}

/** Raw API response (camelCase with string decimals) */
export interface RawSignal {
  id: string;
  instrument: string;
  direction: SignalDirection;
  entryPrice?: string | number;
  entry_price?: string | number;
  stopLoss?: string | number;
  stop_loss?: string | number;
  takeProfit?: string | number;
  take_profit?: string | number;
  positionSize?: string | number;
  position_size?: string | number;
  confidenceScore?: string | number;
  confidence_score?: string | number;
  timeframe: Timeframe;
  orderBlockId?: string | null;
  order_block_id?: string | null;
  strategyId?: string | null;
  strategy_id?: string | null;
  strategyName?: string | null;
  strategy_name?: string | null;
  mode: SignalMode;
  execution?: SignalExecution | null;
  metadata: SignalMetadata | null;
  createdAt?: string;
  created_at?: string;
}

/** Normalize a raw API signal into the canonical Signal shape */
export function normalizeSignal(raw: RawSignal): Signal {
  return {
    id: raw.id,
    instrument: raw.instrument,
    direction: raw.direction,
    entry_price: Number(raw.entryPrice ?? raw.entry_price ?? 0),
    stop_loss: Number(raw.stopLoss ?? raw.stop_loss ?? 0),
    take_profit: Number(raw.takeProfit ?? raw.take_profit ?? 0),
    position_size: Number(raw.positionSize ?? raw.position_size ?? 0),
    confidence_score: Number(raw.confidenceScore ?? raw.confidence_score ?? 0),
    timeframe: raw.timeframe,
    order_block_id: (raw.orderBlockId ?? raw.order_block_id ?? '') as string,
    strategy_id: (raw.strategyId ?? raw.strategy_id ?? '') as string,
    strategy_name: raw.strategyName ?? raw.strategy_name ?? null,
    mode: raw.mode,
    execution: raw.execution ?? null,
    metadata: raw.metadata,
    created_at: raw.createdAt ?? raw.created_at ?? '',
  };
}

/** Resolve a signal's execution outcome, falling back to a mode-based guess
 *  when the backend hasn't attached one (e.g. a just-arrived live WS signal). */
export function signalExecution(s: Signal): SignalExecution {
  if (s.execution) return s.execution;
  if (s.mode === 'backtest') {
    return { status: 'backtest', label: 'Backtest', reason: 'Backtest signal — not for live trading.' };
  }
  if (s.mode === 'forward_test') {
    return {
      status: 'paper',
      label: 'Paper',
      reason: 'Forward-test mode — recorded for analytics but not sent to live execution. Set the strategy to Live to trade it.',
    };
  }
  return { status: 'pending', label: 'Pending', reason: 'Live signal — awaiting execution result.' };
}

/** Display name for the strategy that produced a signal. */
export function signalStrategyName(s: Signal): string {
  if (s.strategy_name) return s.strategy_name;
  if (s.strategy_id) return `Strategy ${s.strategy_id.slice(0, 8)}`;
  return '—';
}
