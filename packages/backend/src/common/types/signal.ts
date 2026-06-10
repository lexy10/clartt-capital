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

export interface Signal {
  id: string;                    // UUID
  instrument: string;            // e.g. "US30"
  direction: SignalDirection;
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  position_size: number;         // lot size
  confidence_score: number;      // 0.0 - 1.0
  timeframe: Timeframe;
  order_block_id: string;
  strategy_id: string;
  mode: SignalMode;
  metadata: SignalMetadata;
  created_at: string;            // ISO 8601
  broker_symbol?: string;        // Resolved from AccountInstrument mapping
}
