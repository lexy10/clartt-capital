import axios, { AxiosInstance, AxiosError, InternalAxiosRequestConfig } from 'axios';
import type {
  ApiClient,
  AlgorithmInfo,
  AlgorithmSource,
  AlgorithmUploadResponse,
  LoginRequest,
  TokenPair,
  PaginationParams,
  TradeHistory,
  Strategy,
  BacktestConfig,
  BacktestResult,
  BacktestTradesResponse,
  SignalQuery,
  SignalPaginatedResponse,
  Watchlist,
  CreateWatchlistDto,
  UpdateWatchlistDto,
  Alert,
  CreateAlertDto,
  SystemStatus,
  ApiErrorResponse,
  AggregateOverviewData,
  AccountPerformanceData,
  TradeDetail,
  ActivityFeedItem,
  StrategyPerformanceData,
  Instrument,
  AccountInstrument,
  AccountStrategy,
  CreateInstrumentDto,
  UpdateInstrumentDto,
} from '../types/api';
import type { EventFilters, PaginatedEventResponse, TradingEvent, ReconstructedState } from '../types/event';
import type { AutopilotState } from '../types/autopilot';
import type { PortfolioSummary, Position } from '../types/trade';
import type { RawSignal, Signal } from '../types/signal';
import { normalizeSignal } from '../types/signal';
import type { Candle } from '../types/candle';
import type { TradingAccount, AccountDetails, CreateAccountDto } from '../types/trading-account';

const TOKEN_KEY = 'us30_access_token';
const REFRESH_KEY = 'us30_refresh_token';

function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

function getStoredRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY);
}

function storeTokens(tokens: TokenPair): void {
  localStorage.setItem(TOKEN_KEY, tokens.access_token);
  localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
}

function clearTokens(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

function extractSignalRows(payload: unknown): RawSignal[] {
  if (Array.isArray(payload)) return payload as RawSignal[];
  if (payload && typeof payload === 'object') {
    const data = (payload as { data?: unknown }).data;
    if (Array.isArray(data)) return data as RawSignal[];
  }
  return [];
}

export function createApiClient(baseURL?: string): ApiClient {
  const http: AxiosInstance = axios.create({
    baseURL: baseURL ?? import.meta.env.VITE_API_URL ?? '/api',
    headers: { 'Content-Type': 'application/json' },
  });

  // Attach access token + admin "view as" context to every request.
  // The asUserId param is set on the request URL/params via localStorage
  // (the source of truth is authStore.viewingAsUserId, which persists there).
  // Backend honors asUserId only when the caller is an admin — non-admins
  // who somehow inject the param get ignored, so this is safe at the edge.
  // We deliberately read localStorage instead of importing the store here
  // to avoid a circular import between the ApiClient and the store that
  // depends on it.
  http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
    const token = getStoredToken();
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    // Don't pollute auth/* or users/* requests — they're already user-scoped
    // by token, and the user-list endpoint must return ALL users for the
    // admin switcher regardless of who they're viewing as.
    const url = config.url ?? '';
    const isAuthOrUsers =
      url.startsWith('/auth') || url === '/users' || url === '/users/me';
    if (!isAuthOrUsers) {
      try {
        const asUserId = localStorage.getItem('dashboard:viewingAsUserId');
        if (asUserId) {
          config.params = { ...(config.params ?? {}), asUserId };
        }
        // Tag the scope this request was issued under so the response
        // interceptor can discard it if the admin user-switched mid-flight.
        // Empty string = "viewing self" (no asUserId param).
        (config as InternalAxiosRequestConfig & { _scopeAtRequest?: string })._scopeAtRequest = asUserId ?? '';
      } catch { /* ignore (e.g. SSR / disabled storage) */ }
    }
    return config;
  });

  // Auto-refresh on 401
  let refreshPromise: Promise<TokenPair> | null = null;

  http.interceptors.response.use(
    (response) => {
      // Drop responses for a scope the admin has already navigated away from.
      // Without this, an in-flight request for trader1 can land AFTER the new
      // request for trader2 and overwrite trader2's empty list with trader1's
      // accounts — making the user-switch look broken. Returning a forever-
      // pending promise prevents the caller's .then from firing, so the store
      // state never gets polluted by the stale response.
      const requestedScope = (response.config as InternalAxiosRequestConfig & {
        _scopeAtRequest?: string;
      })._scopeAtRequest;
      if (requestedScope !== undefined) {
        let currentScope = '';
        try {
          currentScope = localStorage.getItem('dashboard:viewingAsUserId') ?? '';
        } catch { /* ignore */ }
        if (requestedScope !== currentScope) {
          return new Promise<never>(() => { /* never resolves */ });
        }
      }
      return response;
    },
    async (error: AxiosError<ApiErrorResponse>) => {
      const originalRequest = error.config;
      if (!originalRequest || error.response?.status !== 401) {
        return Promise.reject(error);
      }

      // Avoid infinite loop on refresh endpoint itself
      if (originalRequest.url?.includes('/auth/refresh')) {
        clearTokens();
        return Promise.reject(error);
      }

      // Deduplicate concurrent refresh calls
      if (!refreshPromise) {
        const refreshToken = getStoredRefreshToken();
        if (!refreshToken) {
          clearTokens();
          return Promise.reject(error);
        }
        refreshPromise = http
          .post<TokenPair>('/auth/refresh', { refresh_token: refreshToken })
          .then((res) => res.data)
          .finally(() => {
            refreshPromise = null;
          });
      }

      try {
        const tokens = await refreshPromise;
        storeTokens(tokens);
        if (originalRequest.headers) {
          originalRequest.headers.Authorization = `Bearer ${tokens.access_token}`;
        }
        return http(originalRequest);
      } catch {
        clearTokens();
        return Promise.reject(error);
      }
    },
  );

  return {
    auth: {
      async login(credentials: LoginRequest): Promise<TokenPair> {
        const { data } = await http.post<TokenPair>('/auth/login', credentials);
        storeTokens(data);
        return data;
      },
      async refresh(refreshToken: string): Promise<TokenPair> {
        const { data } = await http.post<TokenPair>('/auth/refresh', { refresh_token: refreshToken });
        storeTokens(data);
        return data;
      },
      async logout(): Promise<void> {
        await http.post('/auth/logout');
        clearTokens();
      },
    },

    users: {
      async me(): Promise<{ id: string; email: string; role: string }> {
        const { data } = await http.get<{ id: string; email: string; role: string }>('/users/me');
        return data;
      },
      // Admin-only; returns 403 for non-admins.
      async listAll(): Promise<Array<{ id: string; email: string; role: string }>> {
        const { data } = await http.get<Array<{ id: string; email: string; role: string }>>('/users');
        return data;
      },
    },

    portfolios: {
      async getSummary(): Promise<PortfolioSummary> {
        const { data } = await http.get<PortfolioSummary>('/portfolios/summary');
        return data;
      },
      async getPositions(): Promise<Position[]> {
        const { data } = await http.get<Position[]>('/portfolios/positions');
        return data;
      },
      async getHistory(params: PaginationParams): Promise<TradeHistory> {
        const { data } = await http.get<TradeHistory>('/portfolios/history', { params });
        return data;
      },
    },

    strategies: {
      async list(): Promise<Strategy[]> {
        const { data } = await http.get<Strategy[]>('/strategies');
        return data;
      },
      async getAlgorithms(): Promise<AlgorithmInfo[]> {
        const { data } = await http.get<AlgorithmInfo[]>('/strategies/algorithms');
        return data;
      },
      async getAlgorithmSource(name: string): Promise<AlgorithmSource> {
        const { data } = await http.get<AlgorithmSource>(`/strategies/algorithms/${name}/source`);
        return data;
      },
      async uploadAlgorithm(file: File): Promise<AlgorithmUploadResponse> {
        const formData = new FormData();
        formData.append('file', file);
        const { data } = await http.post<AlgorithmUploadResponse>('/strategies/algorithms/upload', formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        return data;
      },
      async deleteAlgorithm(name: string): Promise<void> {
        await http.delete(`/strategies/algorithms/${name}`);
      },
      async updateAlgorithmSource(name: string, source: string): Promise<AlgorithmUploadResponse> {
        const { data } = await http.patch<AlgorithmUploadResponse>(`/strategies/algorithms/${name}/source`, { source });
        return data;
      },
      async create(dto: { name: string; algorithm?: string; config: Record<string, unknown> }): Promise<Strategy> {
        const { data } = await http.post<Strategy>('/strategies', dto);
        return data;
      },
      async update(id: string, dto: { name?: string; algorithm?: string; config?: Record<string, unknown>; enabled?: boolean }): Promise<Strategy> {
        const { data } = await http.patch<Strategy>(`/strategies/${id}`, dto);
        return data;
      },
      async remove(id: string): Promise<void> {
        await http.delete(`/strategies/${id}`);
      },
      async runBacktest(config: BacktestConfig): Promise<BacktestResult> {
        const { data } = await http.post<BacktestResult>('/strategies/backtest', {
          strategyId: config.strategy_id,
          instrument: config.instrument,
          timeframe: config.timeframe,
          parameters: config.parameters,
          startDate: config.start_date,
          endDate: config.end_date,
        });
        return data;
      },
      async getBacktestResults(strategyId: string): Promise<BacktestResult[]> {
        const { data } = await http.get<BacktestResult[]>(`/strategies/${strategyId}/backtest-results`);
        return data;
      },
      async getBacktestTrades(resultId: string, skip = 0, take = 50): Promise<BacktestTradesResponse> {
        const { data } = await http.get<BacktestTradesResponse>(`/strategies/backtest-results/${resultId}/trades`, {
          params: { skip, take },
        });
        return data;
      },
    },

    signals: {
      async getRecent(params: SignalQuery): Promise<Signal[]> {
        const { data } = await http.get<Signal[] | SignalPaginatedResponse>('/signals', { params });
        const rows = extractSignalRows(data).map(normalizeSignal);
        return params.instrument ? rows.filter((signal) => signal.instrument === params.instrument) : rows;
      },
      async getPaginated(params: { limit?: number; offset?: number }): Promise<SignalPaginatedResponse> {
        const { data } = await http.get<SignalPaginatedResponse>('/signals', { params });
        return data;
      },
    },

    watchlists: {
      async list(): Promise<Watchlist[]> {
        const { data } = await http.get<Watchlist[]>('/watchlists');
        return data;
      },
      async create(watchlist: CreateWatchlistDto): Promise<Watchlist> {
        const { data } = await http.post<Watchlist>('/watchlists', watchlist);
        return data;
      },
      async update(id: string, watchlist: UpdateWatchlistDto): Promise<Watchlist> {
        const { data } = await http.put<Watchlist>(`/watchlists/${id}`, watchlist);
        return data;
      },
      async delete(id: string): Promise<void> {
        await http.delete(`/watchlists/${id}`);
      },
    },

    alerts: {
      async list(): Promise<Alert[]> {
        const { data } = await http.get<Alert[]>('/alerts');
        return data;
      },
      async create(alert: CreateAlertDto): Promise<Alert> {
        const { data } = await http.post<Alert>('/alerts', alert);
        return data;
      },
      async delete(id: string): Promise<void> {
        await http.delete(`/alerts/${id}`);
      },
    },

    marketData: {
      async getCandles(params: { instrument: string; timeframe: string; limit?: number }): Promise<Candle[]> {
        const { limit, ...rest } = params;
        const { data } = await http.get<Candle[]>('/market-data/candles', { params: { ...rest, ...(limit != null ? { count: String(limit) } : {}) } });
        return data;
      },
      async getCandlesByRange(params: { instrument: string; timeframe: string; startDate: string; endDate: string }): Promise<Candle[]> {
        const { data } = await http.get<Candle[]>('/market-data/candles', { params });
        return data;
      },
      async getInstruments(): Promise<string[]> {
        const { data } = await http.get<string[]>('/market-data/instruments');
        return data;
      },
    },

    trades: {
      async list(params?: PaginationParams): Promise<TradeHistory> {
        const { data } = await http.get<TradeHistory>('/trades', { params });
        return data;
      },
    },

    admin: {
      async activateKillSwitch(mode: 'soft' | 'hard' = 'soft'): Promise<void> {
        await http.post('/admin/kill-switch', { active: true, mode });
      },
      async deactivateKillSwitch(): Promise<void> {
        await http.post('/admin/kill-switch', { active: false });
      },
      async getSystemStatus(): Promise<SystemStatus> {
        const { data } = await http.get<SystemStatus>('/admin/status');
        return data;
      },
    },

    autopilot: {
      async getState(accountId: string): Promise<AutopilotState> {
        const { data } = await http.get<AutopilotState>(`/trading-accounts/${accountId}/autopilot`);
        return data;
      },
      async setState(accountId: string, enabled: boolean): Promise<AutopilotState> {
        const { data } = await http.put<AutopilotState>(`/trading-accounts/${accountId}/autopilot`, { enabled });
        return data;
      },
      async getMaster(): Promise<{ enabled: boolean; updatedAt: string }> {
        const { data } = await http.get<{ enabled: boolean; updatedAt: string }>('/autopilot/master');
        return data;
      },
      async setMaster(enabled: boolean): Promise<{ enabled: boolean; updatedAt: string }> {
        const { data } = await http.put<{ enabled: boolean; updatedAt: string }>('/autopilot/master', { enabled });
        return data;
      },
    },

    accounts: {
      async create(dto: CreateAccountDto): Promise<TradingAccount> {
        const { data } = await http.post<TradingAccount>('/accounts', dto);
        return data;
      },
      async list(): Promise<TradingAccount[]> {
        const { data } = await http.get<TradingAccount[]>('/accounts');
        return data;
      },
      async getDetails(id: string): Promise<AccountDetails> {
        const { data } = await http.get<AccountDetails>(`/accounts/${id}/details`);
        return data;
      },
      async getStatus(id: string): Promise<{ state: string; connection_status: string }> {
        const { data } = await http.get<{ state: string; connection_status: string }>(`/accounts/${id}/status`);
        return data;
      },
      async updateLabel(id: string, label: string): Promise<TradingAccount> {
        const { data } = await http.patch<TradingAccount>(`/accounts/${id}/label`, { label });
        return data;
      },
      async remove(id: string): Promise<void> {
        await http.delete(`/accounts/${id}`);
      },
      async deploy(id: string): Promise<void> {
        await http.post(`/accounts/${id}/deploy`);
      },
      async undeploy(id: string): Promise<void> {
        await http.post(`/accounts/${id}/undeploy`);
      },
      async getInstruments(id: string): Promise<AccountInstrument[]> {
        const { data } = await http.get<AccountInstrument[]>(`/accounts/${id}/instruments`);
        return data;
      },
      async setInstruments(id: string, items: { instrumentId: string; brokerSymbol?: string }[]): Promise<AccountInstrument[]> {
        const { data } = await http.put<AccountInstrument[]>(`/accounts/${id}/instruments`, { instruments: items });
        return data;
      },
      async getBrokerSymbols(id: string): Promise<string[]> {
        const { data } = await http.get<string[]>(`/accounts/${id}/broker-symbols`);
        return data;
      },
      async getStrategies(id: string): Promise<AccountStrategy[]> {
        const { data } = await http.get<AccountStrategy[]>(`/accounts/${id}/strategies`);
        return data;
      },
      async setStrategies(id: string, strategyIds: string[]): Promise<AccountStrategy[]> {
        const { data } = await http.put<AccountStrategy[]>(`/accounts/${id}/strategies`, { strategyIds });
        return data;
      },
    },

    instruments: {
      async list(includeInactive?: boolean): Promise<Instrument[]> {
        const { data } = await http.get<Instrument[]>('/instruments', {
          params: includeInactive ? { includeInactive: 'true' } : undefined,
        });
        return data;
      },
      async create(dto: CreateInstrumentDto): Promise<Instrument> {
        const { data } = await http.post<Instrument>('/instruments', dto);
        return data;
      },
      async update(id: string, dto: UpdateInstrumentDto): Promise<Instrument> {
        const { data } = await http.patch<Instrument>(`/instruments/${id}`, dto);
        return data;
      },
      async delete(id: string): Promise<void> {
        await http.delete(`/instruments/${id}`);
      },
    },

    performance: {
      async getOverview(period: string): Promise<AggregateOverviewData> {
        const { data } = await http.get<AggregateOverviewData>('/performance/overview', { params: { period } });
        return data;
      },
      async getAccounts(period: string): Promise<AccountPerformanceData[]> {
        const { data } = await http.get<AccountPerformanceData[]>('/performance/accounts', { params: { period } });
        return data;
      },
      async getAccountTrades(accountId: string, period: string): Promise<TradeDetail[]> {
        const { data } = await http.get<TradeDetail[]>(`/performance/accounts/${accountId}/trades`, { params: { period } });
        return data;
      },
      async getRecentActivity(limit?: number): Promise<ActivityFeedItem[]> {
        const { data } = await http.get<ActivityFeedItem[]>('/performance/activity', { params: limit ? { limit: String(limit) } : {} });
        return data;
      },
      async getStrategies(period: string): Promise<StrategyPerformanceData[]> {
        const { data } = await http.get<StrategyPerformanceData[]>('/performance/strategies', { params: { period } });
        return data;
      },
    },

    events: {
      async getEvents(filters: EventFilters): Promise<PaginatedEventResponse> {
        const { data } = await http.get<PaginatedEventResponse>('/events', { params: filters });
        return data;
      },
      async getEventsByAggregate(aggregateId: string): Promise<TradingEvent[]> {
        const { data } = await http.get<TradingEvent[]>(`/events/aggregates/${aggregateId}`);
        return data;
      },
      async reconstructState(aggregateId: string, timestamp: string): Promise<ReconstructedState> {
        const { data } = await http.get<ReconstructedState>('/events/reconstruct', {
          params: { aggregate_id: aggregateId, timestamp },
        });
        return data;
      },
    },

    reconciliation: {
      async getReports(params?: { account_id?: string; status?: string; page?: string }) {
        const { data } = await http.get('/reconciliation/reports', { params });
        return data;
      },
      async getConfig() {
        const { data } = await http.get('/reconciliation/config');
        return data;
      },
      async updateConfig(dto: Record<string, unknown>) {
        await http.put('/reconciliation/config', dto);
        const { data } = await http.get('/reconciliation/config');
        return data;
      },
      async getAccountStatus(accountId: string) {
        const { data } = await http.get(`/reconciliation/status/${accountId}`);
        return data;
      },
    },

    health: {
      async getStatus() {
        const { data } = await http.get('/health');
        return data;
      },
      async getCircuitBreakers() {
        const { data } = await http.get('/health/circuit-breakers');
        return data;
      },
      async getStrategyEngineHealth() {
        const { data } = await http.get('/health/services/strategy-engine');
        return data;
      },
      async getExecutionEngineHealth() {
        const { data } = await http.get('/health/services/execution-engine');
        return data;
      },
    },
  };
}

// Default singleton instance
export const apiClient = createApiClient();
