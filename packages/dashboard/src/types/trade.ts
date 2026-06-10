export type TradeExecutionStatus = 'filled' | 'rejected' | 'partial' | 'error';

export interface TradeExecutionResult {
  id: string;
  signal_id: string;
  account_id: string;
  order_id: number;
  fill_price: number;
  execution_latency_ms: number;
  status: TradeExecutionStatus;
  rejection_reason?: string;
  slippage: number;
  spread_at_execution: number;
  created_at: string;
}

export interface Position {
  id: string;
  account_id: string;
  trade_id: string;
  instrument: string;
  direction: 'BUY' | 'SELL';
  entry_price: number;
  current_price?: number;
  position_size: number;
  unrealized_pnl?: number;
  opened_at: string;
}

export interface PortfolioSummary {
  equity: number;
  balance: number;
  unrealized_pnl: number;
  open_positions: number;
}
