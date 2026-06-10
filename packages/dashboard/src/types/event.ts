export enum TradingEventType {
  SignalGenerated = 'SignalGenerated',
  SignalPublished = 'SignalPublished',
  RiskEvaluated = 'RiskEvaluated',
  TradeRequested = 'TradeRequested',
  TradeExecuted = 'TradeExecuted',
  TradeFailed = 'TradeFailed',
  PositionOpened = 'PositionOpened',
  PositionUpdated = 'PositionUpdated',
  PositionClosed = 'PositionClosed',
  AutopilotStateChanged = 'AutopilotStateChanged',
  KillSwitchActivated = 'KillSwitchActivated',
  KillSwitchDeactivated = 'KillSwitchDeactivated',
}

export interface TradingEvent {
  id: string;
  eventType: string;
  aggregateId: string;
  sequenceNumber: number;
  correlationId: string | null;
  payload: Record<string, unknown>;
  contextSnapshot: Record<string, unknown> | null;
  sourceService: string;
  createdAt: string;
  schemaVersion: number;
}

export interface EventFilters {
  account_id?: string;
  instrument?: string;
  event_type?: TradingEventType;
  correlation_id?: string;
  aggregate_id?: string;
  start_time?: string;
  end_time?: string;
  page?: number;
  page_size?: number;
  sort?: 'asc' | 'desc';
  include_archived?: boolean;
}

export interface PaginatedEventResponse {
  events: TradingEvent[];
  total_count: number;
  current_page: number;
  page_size: number;
  total_pages: number;
}

export interface ReconstructedState {
  state: Record<string, unknown>;
  events: TradingEvent[];
  event_count: number;
}
