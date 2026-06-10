export interface SignalContextSnapshot {
  current_candle: { open: number; high: number; low: number; close: number; volume: number };
  recent_candles: Array<{ open: number; high: number; low: number; close: number; volume: number }>;
  active_order_blocks: Array<{ price_high: number; price_low: number; direction: string }>;
  current_spread: number;
  strategy_config: Record<string, unknown>;
}

export interface RiskContextSnapshot {
  account_equity: number;
  account_balance: number;
  open_position_count: number;
  current_lot_exposure: number;
  daily_loss: number;
  risk_thresholds: {
    max_risk_per_trade: number;
    max_daily_loss: number;
    max_open_positions: number;
    max_lot_exposure: number;
  };
}

export interface ExecutionContextSnapshot {
  broker_connected: boolean;
  current_spread: number;
  bid_price: number;
  ask_price: number;
  queue_depth: number;
}

export interface AutopilotContextSnapshot {
  open_positions_per_account: Record<string, number>;
  pending_signals_count: number;
  kill_switch_active: boolean;
}
