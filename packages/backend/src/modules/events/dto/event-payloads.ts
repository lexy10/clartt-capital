export interface SignalGeneratedPayload {
  signal_id: string;
  instrument: string;
  direction: 'BUY' | 'SELL';
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  position_size: number;
  confidence_score: number;
  timeframe: string;
  strategy_id: string;
  algorithm_name: string;
  order_block_id: string | null;
}

export interface SignalPublishedPayload {
  signal_id: string;
  instrument: string;
  direction: 'BUY' | 'SELL';
  publish_timestamp: string;
}

export interface RiskEvaluatedPayload {
  signal_id: string;
  account_id: string;
  passed: boolean;
  rules_evaluated: Array<{ rule: string; result: boolean; threshold: number }>;
  rejection_reason: string | null;
}

export interface TradeRequestedPayload {
  signal_id: string;
  account_id: string;
  instrument: string;
  direction: 'BUY' | 'SELL';
  requested_size: number;
  broker_order_id: string | null;
}

export interface TradeExecutedPayload {
  signal_id: string;
  account_id: string;
  trade_id: string;
  fill_price: number;
  position_size: number;
  execution_latency_ms: number;
  slippage: number;
  spread_at_execution: number;
}

export interface TradeFailedPayload {
  signal_id: string;
  account_id: string;
  failure_reason: string;
  error_code: string;
  retry_count: number;
}

export interface PositionOpenedPayload {
  position_id: string;
  account_id: string;
  trade_id: string;
  instrument: string;
  direction: 'BUY' | 'SELL';
  entry_price: number;
  position_size: number;
}

export interface PositionUpdatedPayload {
  position_id: string;
  account_id: string;
  current_price: number;
  unrealized_pnl: number;
  update_reason: string;
}

export interface PositionClosedPayload {
  position_id: string;
  account_id: string;
  exit_price: number;
  realized_pnl: number;
  close_reason: string;
  duration_seconds: number;
}

export interface AutopilotStateChangedPayload {
  scope: 'master' | 'account';
  account_id: string | null;
  previous_state: boolean;
  new_state: boolean;
  changed_by: string;
}

export interface KillSwitchActivatedPayload {
  activated_by: string;
  reason: string;
}

export interface KillSwitchDeactivatedPayload {
  deactivated_by: string;
  duration_active_seconds: number;
}
