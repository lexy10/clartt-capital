export type TradeExecutionStatus = 'filled' | 'rejected' | 'partial' | 'error';

export interface TradeExecutionResult {
  id: string;                    // UUID
  signal_id: string;
  account_id: string;
  order_id: number;              // Broker order ID
  fill_price: number;
  execution_latency_ms: number;
  status: TradeExecutionStatus;
  rejection_reason?: string;
  slippage: number;
  spread_at_execution: number;
  created_at: string;            // ISO 8601
}
