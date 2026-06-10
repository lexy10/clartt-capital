export interface AggregateOverviewDto {
  returnMultiplier: number;
  periodPercentChange: number;
  totalBalance: number;
  totalEquity: number;
  todayPnl: number;
  winRate: number;
  profitFactor: number;
  avgRiskReward: number;
  maxDrawdown: number;
  totalTrades: number;
  // ── Multi-broker / multi-account extensions ──
  accountsCount: number;
  openPositionsCount: number;
  totalExposure: number;
  byBroker: BrokerSummary[];
  topInstruments: InstrumentPnl[];
}

/** Per-broker rollup for the multi-broker dashboard summary. */
export interface BrokerSummary {
  provider: string;        // 'deriv' | 'metaapi' | 'alpaca' | 'binance' | 'ibkr' | 'stub'
  accountsCount: number;
  totalEquity: number;
  totalBalance: number;
  periodPnl: number;
  openPositions: number;
  // ── Enriched: aggregate trade quality + most-traded symbol ──
  winRate?: number | null;        // percent — null if no closed trades
  totalTrades?: number | null;
  topInstrument?: string | null;  // best-performing symbol for this broker
}

export interface InstrumentPnl {
  instrument: string;
  totalPnl: number;
  tradeCount: number;
  // ── Enriched: per-instrument quality stats ──
  winRate?: number | null;
  winningTrades?: number | null;
  losingTrades?: number | null;
}

export interface SparklinePoint {
  timestamp: string;
  equity: number;
}

export interface AccountPerformanceDto {
  accountId: string;
  accountLabel: string;
  returnMultiplier: number;
  periodPercentChange: number;
  balance: number;
  equity: number;
  totalTrades: number;
  winningTrades: number;
  losingTrades: number;
  bestTrade: { pnl: number; instrument: string } | null;
  worstTrade: { pnl: number; instrument: string } | null;
  instrumentBreakdown: InstrumentPnl[];
  equitySparkline: SparklinePoint[];
  // ── Enriched: who/where the account lives + live activity ──
  brokerProvider?: string | null;
  accountKind?: string | null;
  openPositionsCount?: number;
  periodPnl?: number;
  autopilotEnabled?: boolean | null;
  lastTradeAt?: string | null;
}

export interface TradeDetailDto {
  tradeId: string;
  entryTime: string;
  exitTime: string;
  instrument: string;
  direction: string;
  lotSize: number;
  entryPrice: number;
  exitPrice: number;
  pnlDollars: number;
  pnlPips: number;
  duration: number;
}


export interface ActivityFeedItemDto {
  id: string;
  type: 'trade_opened' | 'trade_closed' | 'signal_generated';
  instrument: string;
  direction: string;
  detail: string;
  timestamp: string;
  accountLabel?: string;
}

export interface StrategyTradeDto {
  tradeId: string;
  instrument: string;
  direction: string;
  entryTime: string;
  exitTime: string;
  profitLoss: number;
  actualR: number;
  plannedRR: number;
  actualRR: number;
}

export interface StrategyPerformanceDto {
  strategyId: string;
  strategyName: string;
  cumulativeR: number;
  avgR: number;
  winRate: number;
  totalTrades: number;
  avgPlannedRR: number;
  avgActualRR: number;
  trades: StrategyTradeDto[];
}
