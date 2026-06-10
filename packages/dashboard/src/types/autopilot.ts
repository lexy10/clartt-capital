// --- Chart rendering types ---

export interface AutopilotState {
  accountId: string;
  enabled: boolean;
  updatedAt: string; // ISO 8601
}

export type OverlayDirection = 'bullish' | 'bearish' | 'neutral';

export interface EntryZone {
  priceHigh: number;
  priceLow: number;
  startTime: string; // ISO 8601
  direction: OverlayDirection;
  signalId?: string;
}

export interface ExitZone {
  type: 'stop_loss' | 'take_profit';
  price: number;
  startTime: string; // ISO 8601
  direction: OverlayDirection;
  signalId?: string;
}

export interface OrderBlockZone {
  priceHigh: number;
  priceLow: number;
  startTime: string; // ISO 8601
  endTime?: string; // ISO 8601
  direction: OverlayDirection;
}

export interface TradeMarker {
  id: string;
  signalId: string;
  direction: 'BUY' | 'SELL';
  entryPrice: number;
  exitPrice?: number;
  profitLoss?: number;
  exitReason?: 'stop_loss' | 'take_profit' | 'strategy_exit' | 'manual';
  executedAt: string; // ISO 8601
  type: 'entry' | 'exit';
}

export type OverlayData =
  | ({ kind: 'entry_zone' } & EntryZone)
  | ({ kind: 'exit_zone' } & ExitZone)
  | ({ kind: 'order_block' } & OrderBlockZone);

// --- WebSocket event payloads ---

export interface AutopilotStateChangeEvent {
  type: 'state_change';
  accountId: string;
  enabled: boolean;
  updatedAt: string; // ISO 8601
}

export interface StrategyOverlayEvent {
  type: 'strategy_overlay';
  accountId: string;
  overlays: Array<{
    kind: 'entry_zone' | 'exit_zone' | 'order_block';
    priceHigh?: number;
    priceLow?: number;
    price?: number;
    type?: 'stop_loss' | 'take_profit';
    startTime: string;
    endTime?: string;
    direction: OverlayDirection;
    signalId?: string;
  }>;
}

export interface TradeEntryEvent {
  type: 'trade_entry';
  accountId: string;
  trade: {
    id: string;
    signalId: string;
    direction: 'BUY' | 'SELL';
    entryPrice: number;
    stopLoss: number;
    takeProfit: number;
    positionSize: number;
    executedAt: string;
  };
}

export interface TradeExitEvent {
  type: 'trade_exit';
  accountId: string;
  trade: {
    id: string;
    signalId: string;
    direction: 'BUY' | 'SELL';
    entryPrice: number;
    exitPrice: number;
    profitLoss: number;
    exitReason: 'stop_loss' | 'take_profit' | 'strategy_exit' | 'manual';
    executedAt: string;
  };
}

export type AutopilotEvent =
  | AutopilotStateChangeEvent
  | StrategyOverlayEvent
  | TradeEntryEvent
  | TradeExitEvent;
