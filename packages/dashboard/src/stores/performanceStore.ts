import { create } from 'zustand';
import type { AggregateOverviewData, AccountPerformanceData, TradeDetail, ActivityFeedItem, StrategyPerformanceData } from '../types/api';
import { apiClient } from '../services/ApiClient';
import { wsManager } from '../services/WebSocketManager';

export type TimePeriod = 'today' | 'this_week' | 'this_month' | 'all_time';

interface PerformanceStore {
  period: TimePeriod;
  activeTab: 'accounts' | 'strategies';
  overview: AggregateOverviewData | null;
  accounts: AccountPerformanceData[];
  strategies: StrategyPerformanceData[];
  drillDown: { accountId: string; trades: TradeDetail[] } | null;
  activityFeed: ActivityFeedItem[];

  overviewLoading: boolean;
  accountsLoading: boolean;
  strategiesLoading: boolean;
  drillDownLoading: boolean;
  overviewError: string | null;
  accountsError: string | null;
  strategiesError: string | null;
  drillDownError: string | null;

  /** Auto-refresh interval for the Live Desk multi-account summary.
   *  0 = off. Persisted to localStorage so the user's choice survives reloads. */
  overviewRefreshMs: number;
  setOverviewRefreshMs: (ms: number) => void;

  setActiveTab: (tab: 'accounts' | 'strategies') => void;
  setPeriodAndFetch: (period: TimePeriod) => Promise<void>;
  /** `silent: true` skips flipping `loading: true` — used for background
   *  refreshes (period change, auto-refresh, WebSocket sync) so the UI
   *  swaps numbers in place instead of flashing skeletons. */
  fetchOverview: (silent?: boolean) => Promise<void>;
  fetchAccounts: (silent?: boolean) => Promise<void>;
  fetchStrategyPerformance: (silent?: boolean) => Promise<void>;
  fetchDrillDown: (accountId: string) => Promise<void>;
  fetchActivityFeed: () => Promise<void>;
  closeDrillDown: () => void;
  subscribeToSync: () => void;
  unsubscribeFromSync: () => void;
}

const REFRESH_KEY = 'clartt:overviewRefreshMs';

function loadInitialRefreshMs(): number {
  try {
    const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(REFRESH_KEY) : null;
    if (raw === null) return 60_000; // default: 1 minute
    const n = Number(raw);
    return Number.isFinite(n) && n >= 0 ? n : 60_000;
  } catch {
    return 60_000;
  }
}

export const usePerformanceStore = create<PerformanceStore>((set, get) => ({
  period: 'today',
  activeTab: 'accounts',
  overview: null,
  accounts: [],
  strategies: [],
  drillDown: null,
  activityFeed: [],

  overviewLoading: false,
  accountsLoading: false,
  strategiesLoading: false,
  drillDownLoading: false,
  overviewError: null,
  accountsError: null,
  strategiesError: null,
  drillDownError: null,

  overviewRefreshMs: loadInitialRefreshMs(),
  setOverviewRefreshMs: (ms: number) => {
    set({ overviewRefreshMs: ms });
    try {
      if (typeof localStorage !== 'undefined') localStorage.setItem(REFRESH_KEY, String(ms));
    } catch { /* swallow */ }
  },

  setActiveTab: (tab: 'accounts' | 'strategies') => {
    set({ activeTab: tab });
  },

  setPeriodAndFetch: async (period: TimePeriod) => {
    set({ period });
    // Silent refresh — keep the old numbers visible until the new ones land.
    const promises: Promise<void>[] = [get().fetchOverview(true), get().fetchAccounts(true)];
    if (get().strategies.length > 0) {
      promises.push(get().fetchStrategyPerformance(true));
    }
    await Promise.all(promises);
  },

  fetchOverview: async (silent = false) => {
    if (!silent) set({ overviewLoading: true, overviewError: null });
    else set({ overviewError: null });
    try {
      const overview = await apiClient.performance.getOverview(get().period);
      set({ overview, overviewLoading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load performance overview';
      set({ overviewError: message, overviewLoading: false });
    }
  },

  fetchAccounts: async (silent = false) => {
    if (!silent) set({ accountsLoading: true, accountsError: null });
    else set({ accountsError: null });
    try {
      const accounts = await apiClient.performance.getAccounts(get().period);
      set({ accounts, accountsLoading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load account performance';
      set({ accountsError: message, accountsLoading: false });
    }
  },

  fetchStrategyPerformance: async (silent = false) => {
    if (!silent) set({ strategiesLoading: true, strategiesError: null });
    else set({ strategiesError: null });
    try {
      const strategies = await apiClient.performance.getStrategies(get().period);
      set({ strategies, strategiesLoading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load strategy performance';
      set({ strategiesError: message, strategiesLoading: false });
    }
  },

  fetchDrillDown: async (accountId: string) => {
    set({ drillDownLoading: true, drillDownError: null });
    try {
      const trades = await apiClient.performance.getAccountTrades(accountId, get().period);
      set({ drillDown: { accountId, trades }, drillDownLoading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load trade details';
      set({ drillDownError: message, drillDownLoading: false });
    }
  },

  fetchActivityFeed: async () => {
    try {
      const activityFeed = await apiClient.performance.getRecentActivity(10);
      set({ activityFeed });
    } catch {
      // Silently fail — activity feed is non-critical
    }
  },

  closeDrillDown: () => {
    set({ drillDown: null });
  },

  subscribeToSync: () => {
    const subId = wsManager.subscribe('account_sync', () => {
      // Silent — sync events fire constantly during normal trading; we
      // shouldn't flash skeletons every time a tick lands.
      get().fetchOverview(true);
      get().fetchAccounts(true);
    });
    set({ _syncSubId: subId } as Partial<PerformanceStore>);
  },

  unsubscribeFromSync: () => {
    const subId = (get() as PerformanceStore & { _syncSubId?: string })._syncSubId;
    if (subId) {
      wsManager.unsubscribe(subId);
    }
  },
}));
