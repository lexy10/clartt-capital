import { AutopilotState } from './autopilot';
import { PortfolioSummary, Position } from './trade';
import { Signal } from './signal';
import { Candle } from './candle';
import { TradingAccount, AccountDetails, CreateAccountDto } from './trading-account';
import { EventFilters, PaginatedEventResponse, TradingEvent, ReconstructedState } from './event';

export interface LoginRequest {
  email: string;
  password: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
}

export interface PaginationParams {
  page?: number;
  limit?: number;
}

export interface TradeHistoryItem {
  id: string;
  signal_id: string;
  account_id: string;
  direction: 'BUY' | 'SELL';
  entry_price: number;
  exit_price?: number;
  position_size: number;
  profit_loss?: number;
  status: string;
  opened_at?: string;
  closed_at?: string;
  created_at: string;
}

export interface TradeHistory {
  items: TradeHistoryItem[];
  total: number;
  page: number;
  limit: number;
}

export interface AlgorithmInfo {
  name: string;
  description: string;
  default_params: Record<string, unknown>;
  param_schema: Record<string, unknown>;
}

export interface AlgorithmSource {
  name: string;
  source: string;
  filename: string;
}

export interface AlgorithmUploadResponse {
  name: string;
  message: string;
}

export interface Strategy {
  id: string;
  name: string;
  algorithm: string;
  config: Record<string, unknown>;
  enabled?: boolean;
  createdAt?: string;
  updatedAt?: string;
  // snake_case aliases (kept for backward compat)
  created_at?: string;
  updated_at?: string;
}

export interface BacktestConfig {
  strategy_id: string;
  instrument: string;
  timeframe?: string;
  start_date: string;
  end_date: string;
  /** Mixed bag: backtest engine params (initial_capital, spread, slippage, max_lot_size,
   *  commission_per_trade) AND algorithm param overrides (structure_lookback, swing_length, etc.)
   *  The backend splits these automatically. */
  parameters: Record<string, unknown>;
}

export interface BacktestResult {
  id: string;
  strategy_id?: string;
  strategyId?: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  config?: {
    strategyId?: string;
    instrument?: string;
    timeframe?: string;
    parameters?: Record<string, unknown>;
    startDate?: string | null;
    endDate?: string | null;
  };
  win_rate?: number | null;
  winRate?: number | null;
  max_drawdown?: number | null;
  maxDrawdown?: number | null;
  sharpe_ratio?: number | null;
  sharpeRatio?: number | null;
  profit_factor?: number | null;
  profitFactor?: number | null;
  expectancy?: number | null;
  total_trades?: number | null;
  totalTrades?: number | null;
  winning_trades?: number | null;
  winningTrades?: number | null;
  losing_trades?: number | null;
  losingTrades?: number | null;
  gross_profit?: number | null;
  grossProfit?: number | null;
  gross_loss?: number | null;
  grossLoss?: number | null;
  net_profit?: number | null;
  netProfit?: number | null;
  average_rr?: number | null;
  averageRr?: number | null;
  equity_curve?: number[] | null;
  equityCurve?: number[] | null;
  trade_results?: unknown[] | null;
  tradeResults?: unknown[] | null;
  error_message?: string | null;
  errorMessage?: string | null;
  created_at?: string;
  createdAt?: string;
}

export interface BacktestUpdateEvent {
  result_id: string;
  strategy_id: string;
  status: 'running' | 'completed' | 'failed';
  win_rate?: number;
  max_drawdown?: number;
  sharpe_ratio?: number;
  profit_factor?: number;
  expectancy?: number;
  total_trades?: number;
  winning_trades?: number;
  losing_trades?: number;
  gross_profit?: number;
  gross_loss?: number;
  net_profit?: number;
  average_rr?: number;
  equity_curve?: number[];
  trade_results?: unknown[];
  error_message?: string;
}

export interface BacktestTrade {
  id: string;
  backtestResultId: string;
  signalId: string;
  direction: 'BUY' | 'SELL';
  entryPrice: string;
  exitPrice: string;
  stopLoss: string | null;
  takeProfit: string | null;
  positionSize: string;
  profitLoss: string;
  rewardRisk: string | null;
  entryTime: string;
  exitTime: string;
  tradeIndex: number;
}

export interface BacktestTradesResponse {
  items: BacktestTrade[];
  total: number;
}

export interface SignalQuery {
  instrument?: string;
  limit?: number;
  since?: string;
}

export interface SignalPaginatedResponse {
  data: Signal[];
  total: number;
  limit: number;
  offset: number;
}

export interface Watchlist {
  id: string;
  user_id: string;
  name: string;
  instruments: string[];
  created_at: string;
  updated_at: string;
}

export interface CreateWatchlistDto {
  name: string;
  instruments: string[];
}

export interface UpdateWatchlistDto {
  name?: string;
  instruments?: string[];
}

export interface Alert {
  id: string;
  user_id: string;
  instrument: string;
  condition_type: string;
  condition_value: Record<string, unknown>;
  is_active: boolean;
  triggered_at?: string;
  created_at: string;
}

export interface CreateAlertDto {
  instrument: string;
  condition_type: string;
  condition_value: Record<string, unknown>;
}

export interface SystemStatus {
  kill_switch_active: boolean;
  services: {
    backend: 'healthy' | 'degraded' | 'down';
    strategy_engine: 'healthy' | 'degraded' | 'down';
    execution_engine: 'healthy' | 'degraded' | 'down';
  };
  uptime_seconds: number;
}

export interface ApiErrorResponse {
  error: string;
  code: string;
  details?: Record<string, unknown>;
}

export interface AdminUser {
  id: string;
  email: string;
  role: string;
  isActive: boolean;
  createdAt: string;
}

export interface TestSignalStep {
  step: string;
  ok: boolean;
  detail: string;
}

/** Result of POST /accounts/:id/test-signal — a per-gate trace of a synthetic
 *  entry pushed through the execution pipeline. */
export interface TestSignalResult {
  account_id: string;
  steps: TestSignalStep[];
  wouldTrade: boolean;
  placed: boolean;
  signal?: { direction: string; entry: number; stopLoss: number; takeProfit: number };
  execution?: { status: string; orderId: number; fillPrice: number; rejectionReason?: string | null };
}

/** The signed-in user, as returned by GET /users/me. */
export interface CurrentUserDto {
  id: string;
  email: string;
  role: string;
  theme?: { mode?: string; accent?: string } | null;
}

export interface ApiClient {
  auth: {
    login(credentials: LoginRequest): Promise<TokenPair>;
    refresh(refreshToken: string): Promise<TokenPair>;
    logout(): Promise<void>;
  };
  users: {
    me(): Promise<CurrentUserDto>;
    updateMe(dto: { email?: string; theme?: { mode?: string; accent?: string } }): Promise<CurrentUserDto>;
    changeMyPassword(currentPassword: string, newPassword: string): Promise<void>;
    listAll(): Promise<AdminUser[]>;
    create(dto: { email: string; password: string; role: string }): Promise<AdminUser>;
    updateRole(id: string, role: string): Promise<AdminUser>;
    setActive(id: string, isActive: boolean): Promise<AdminUser>;
    resetPassword(id: string, password: string): Promise<void>;
  };
  portfolios: {
    getSummary(): Promise<PortfolioSummary>;
    getPositions(): Promise<Position[]>;
    getHistory(params: PaginationParams): Promise<TradeHistory>;
  };
  strategies: {
    list(): Promise<Strategy[]>;
    getAlgorithms(): Promise<AlgorithmInfo[]>;
    getAlgorithmSource(name: string): Promise<AlgorithmSource>;
    uploadAlgorithm(file: File): Promise<AlgorithmUploadResponse>;
    deleteAlgorithm(name: string): Promise<void>;
    updateAlgorithmSource(name: string, source: string): Promise<AlgorithmUploadResponse>;
    create(dto: { name: string; algorithm?: string; config: Record<string, unknown> }): Promise<Strategy>;
    update(id: string, dto: { name?: string; algorithm?: string; config?: Record<string, unknown>; enabled?: boolean }): Promise<Strategy>;
    remove(id: string): Promise<void>;
    runBacktest(config: BacktestConfig): Promise<BacktestResult>;
    getBacktestResults(strategyId: string): Promise<BacktestResult[]>;
    getBacktestTrades(resultId: string, skip?: number, take?: number): Promise<BacktestTradesResponse>;
  };
  signals: {
    getRecent(params: SignalQuery): Promise<Signal[]>;
    getPaginated(params: { limit?: number; offset?: number }): Promise<SignalPaginatedResponse>;
  };
  watchlists: {
    list(): Promise<Watchlist[]>;
    create(watchlist: CreateWatchlistDto): Promise<Watchlist>;
    update(id: string, watchlist: UpdateWatchlistDto): Promise<Watchlist>;
    delete(id: string): Promise<void>;
  };
  alerts: {
    list(): Promise<Alert[]>;
    create(alert: CreateAlertDto): Promise<Alert>;
    delete(id: string): Promise<void>;
  };
  marketData: {
    getCandles(params: { instrument: string; timeframe: string; limit?: number }): Promise<Candle[]>;
    getCandlesByRange(params: { instrument: string; timeframe: string; startDate: string; endDate: string }): Promise<Candle[]>;
    getInstruments(): Promise<string[]>;
  };
  trades: {
    list(params?: PaginationParams): Promise<TradeHistory>;
  };
  admin: {
    activateKillSwitch(mode?: 'soft' | 'hard'): Promise<void>;
    deactivateKillSwitch(): Promise<void>;
    getSystemStatus(): Promise<SystemStatus>;
  };
  autopilot: {
    getState(accountId: string): Promise<AutopilotState>;
    setState(accountId: string, enabled: boolean): Promise<AutopilotState>;
    getMaster(): Promise<{ enabled: boolean; updatedAt: string }>;
    setMaster(enabled: boolean): Promise<{ enabled: boolean; updatedAt: string }>;
  };
  accounts: {
    create(dto: CreateAccountDto): Promise<TradingAccount>;
    list(): Promise<TradingAccount[]>;
    getDetails(id: string): Promise<AccountDetails>;
    getStatus(id: string): Promise<{ state: string; connection_status: string }>;
    updateLabel(id: string, label: string): Promise<TradingAccount>;
    updateDerivToken(id: string, dto: { derivApiToken: string; derivLoginId?: string }): Promise<TradingAccount>;
    testSignal(id: string, dto: { instrument: string; direction?: string; placeLive?: boolean }): Promise<TestSignalResult>;
    remove(id: string): Promise<void>;
    deploy(id: string): Promise<void>;
    undeploy(id: string): Promise<void>;
    getInstruments(id: string): Promise<AccountInstrument[]>;
    setInstruments(id: string, items: { instrumentId: string; brokerSymbol?: string }[]): Promise<AccountInstrument[]>;
    getBrokerSymbols(id: string): Promise<string[]>;
    getStrategies(id: string): Promise<AccountStrategy[]>;
    setStrategies(id: string, strategyIds: string[]): Promise<AccountStrategy[]>;
  };
  instruments: {
    list(includeInactive?: boolean): Promise<Instrument[]>;
    create(dto: CreateInstrumentDto): Promise<Instrument>;
    update(id: string, dto: UpdateInstrumentDto): Promise<Instrument>;
    delete(id: string): Promise<void>;
  };
  performance: {
    getOverview(period: string): Promise<AggregateOverviewData>;
    getAccounts(period: string): Promise<AccountPerformanceData[]>;
    getAccountTrades(accountId: string, period: string): Promise<TradeDetail[]>;
    getRecentActivity(limit?: number): Promise<ActivityFeedItem[]>;
    getStrategies(period: string): Promise<StrategyPerformanceData[]>;
  };
  events: {
    getEvents(filters: EventFilters): Promise<PaginatedEventResponse>;
    getEventsByAggregate(aggregateId: string): Promise<TradingEvent[]>;
    reconstructState(aggregateId: string, timestamp: string): Promise<ReconstructedState>;
  };
  reconciliation: {
    getReports(params?: { account_id?: string; status?: string; page?: string }): Promise<{ data: unknown[]; total: number; page: number; totalPages: number }>;
    getConfig(): Promise<unknown>;
    updateConfig(dto: Record<string, unknown>): Promise<unknown>;
    getAccountStatus(accountId: string): Promise<unknown>;
  };
  health: {
    getStatus(): Promise<unknown>;
    getCircuitBreakers(): Promise<{ circuitBreakers: unknown[]; remoteBreakers: unknown[] }>;
    getStrategyEngineHealth(): Promise<unknown>;
    getExecutionEngineHealth(): Promise<unknown>;
  };
}

// Performance API response types (matching backend DTOs)
export interface AggregateOverviewData {
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
  accountsCount?: number;
  openPositionsCount?: number;
  totalExposure?: number;
  byBroker?: BrokerSummary[];
  topInstruments?: InstrumentPnl[];
}

export interface BrokerSummary {
  provider: string;
  accountsCount: number;
  totalEquity: number;
  totalBalance: number;
  periodPnl: number;
  openPositions: number;
  // ── Enriched fields ──
  winRate?: number | null;
  totalTrades?: number | null;
  topInstrument?: string | null;
}

export interface InstrumentPnl {
  instrument: string;
  totalPnl: number;
  tradeCount: number;
  // ── Enriched fields ──
  winRate?: number | null;
  winningTrades?: number | null;
  losingTrades?: number | null;
}

export interface SparklinePoint {
  timestamp: string;
  equity: number;
}

export interface AccountPerformanceData {
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
  // ── Enriched: who/where + what's live right now ──
  brokerProvider?: string | null;       // 'deriv' | 'metaapi' | ...
  accountKind?: string | null;          // 'personal' | 'prop' | 'demo'
  openPositionsCount?: number;
  periodPnl?: number;                   // P&L for the selected period
  autopilotEnabled?: boolean | null;
  lastTradeAt?: string | null;          // ISO timestamp of most recent trade
}

export interface TradeDetail {
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

export interface ActivityFeedItem {
  id: string;
  type: 'trade_opened' | 'trade_closed' | 'signal_generated';
  instrument: string;
  direction: string;
  detail: string;
  timestamp: string;
  accountLabel?: string;
}

export interface StrategyTradeData {
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

export interface StrategyPerformanceData {
  strategyId: string;
  strategyName: string;
  cumulativeR: number;
  avgR: number;
  winRate: number;
  totalTrades: number;
  avgPlannedRR: number;
  avgActualRR: number;
  trades: StrategyTradeData[];
}

export interface Instrument {
  id: string;
  symbol: string;
  displayName: string;
  type: 'index' | 'commodity' | 'synthetic';
  isActive: boolean;
  contractSize: number;
  pipSize: number;
  pipValue: number;
  minLot: number;
  lotStep: number;
  leverage: number;
}

export interface AccountInstrument {
  id: string;
  accountId: string;
  instrumentId: string;
  brokerSymbol: string;
  instrument: Instrument;
  createdAt: string;
}

export interface AccountStrategy {
  id: string;
  accountId: string;
  strategyId: string;
  strategy: Strategy;
  createdAt: string;
}


export const ROUTES = {
  HOME: '/',
  LIVE_DESK: '/',
  DASHBOARD: '/dashboard',
  CHART: '/chart',
  SIGNALS: '/signals',
  POSITIONS: '/positions',
  ACCOUNTS: '/accounts',
  STRATEGY: '/strategy',
  INSTRUMENTS: '/admin/instruments',
  ALGORITHMS: '/admin/algorithms',
  RECONCILIATION: '/reconciliation',
  EVENTS: '/events',
  PROFILE: '/profile',
  USERS: '/admin/users',
  HEALTH: '/health',
  AGENTS: '/agents',
  LOGIN: '/login',
} as const;

export interface CreateInstrumentDto {
  symbol: string;
  displayName: string;
  type: 'index' | 'commodity' | 'synthetic';
  contractSize?: number;
  pipSize?: number;
  pipValue?: number;
  minLot?: number;
  lotStep?: number;
  leverage?: number;
}

export interface UpdateInstrumentDto {
  displayName?: string;
  type?: 'index' | 'commodity' | 'synthetic';
  isActive?: boolean;
  contractSize?: number;
  pipSize?: number;
  pipValue?: number;
  minLot?: number;
  lotStep?: number;
  leverage?: number;
}
