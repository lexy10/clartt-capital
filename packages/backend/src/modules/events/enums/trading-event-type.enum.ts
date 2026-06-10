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
