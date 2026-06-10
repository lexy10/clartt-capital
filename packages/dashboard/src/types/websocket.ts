import { AutopilotStateChangeEvent, StrategyOverlayEvent, TradeEntryEvent, TradeExitEvent } from './autopilot';
import { Candle } from './candle';
import { Signal } from './signal';
import { TradeExecutionResult } from './trade';

export interface AlertTriggered {
  id: string;
  instrument: string;
  condition_type: string;
  triggered_at: string;
  message: string;
}

export interface RiskAlert {
  account_id: string;
  rule: string;
  message: string;
  timestamp: string;
}

export interface KillSwitchStatus {
  is_active: boolean;
  activated_at?: string;
}

export type WSEvent =
  | { type: 'candle'; data: Candle }
  | { type: 'signal'; data: Signal }
  | { type: 'trade'; data: TradeExecutionResult }
  | { type: 'alert'; data: AlertTriggered }
  | { type: 'risk_alert'; data: RiskAlert }
  | { type: 'kill_switch'; data: KillSwitchStatus }
  | { type: 'autopilot_state_change'; data: AutopilotStateChangeEvent }
  | { type: 'autopilot_strategy_overlay'; data: StrategyOverlayEvent }
  | { type: 'autopilot_trade_entry'; data: TradeEntryEvent }
  | { type: 'autopilot_trade_exit'; data: TradeExitEvent };
