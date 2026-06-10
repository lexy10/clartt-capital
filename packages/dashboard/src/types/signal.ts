import { Timeframe } from './timeframe';

export type SignalDirection = 'BUY' | 'SELL';
export type SignalMode = 'backtest' | 'forward_test' | 'live';
export type BOSType = 'bullish' | 'bearish';

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
  mode: SignalMode;
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
  mode: SignalMode;
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
    mode: raw.mode,
    metadata: raw.metadata,
    created_at: raw.createdAt ?? raw.created_at ?? '',
  };
}
