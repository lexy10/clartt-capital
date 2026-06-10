import { create } from 'zustand';
import { apiClient } from '../services/ApiClient';
import { wsManager } from '../services/WebSocketManager';

export interface ReconciliationReport {
  id: string;
  accountId: string;
  cycleTimestamp: string;
  discrepancies: Array<{ type: string; severity: string; details: Record<string, unknown> }>;
  autoCorrectionsApplied: Array<{ type: string; success: boolean }>;
  durationMs: number;
  status: string;
  errorMessage: string | null;
  createdAt: string;
}

export interface ReconciliationConfig {
  reconciliationIntervalSeconds: number;
  balanceDriftThreshold: number;
  equityDriftThreshold: number;
  positionSizeDriftThreshold: number;
  autoCorrectPhantomPositions: boolean;
  autoCorrectMissingPositions: boolean;
  autoCorrectBalanceDrift: boolean;
  escalationCycleCount: number;
}

export interface AccountReconciliationStatus {
  accountId: string;
  lastCycleAt: string | null;
  lastStatus: string | null;
  consecutiveFailures: number;
}

interface ReconciliationState {
  reports: ReconciliationReport[];
  totalReports: number;
  currentPage: number;
  totalPages: number;
  config: ReconciliationConfig | null;
  accountStatuses: Record<string, AccountReconciliationStatus>;
  recentAlert: { accountId: string; discrepancies: Array<{ type: string; severity: string }> } | null;
  alertSubId: string | null;
  loading: boolean;
  error: string | null;
  fetchReports: (params?: { account_id?: string; status?: string; page?: number }) => Promise<void>;
  fetchConfig: () => Promise<void>;
  updateConfig: (dto: Partial<ReconciliationConfig>) => Promise<void>;
  fetchAccountStatus: (accountId: string) => Promise<void>;
  subscribeToAlerts: () => void;
  unsubscribeFromAlerts: () => void;
  dismissAlert: () => void;
}

export const useReconciliationStore = create<ReconciliationState>((set, get) => ({
  reports: [],
  totalReports: 0,
  currentPage: 1,
  totalPages: 1,
  config: null,
  accountStatuses: {},
  recentAlert: null,
  alertSubId: null,
  loading: false,
  error: null,

  fetchReports: async (params) => {
    set({ loading: true, error: null });
    try {
      const query: { account_id?: string; status?: string; page?: string } = {};
      if (params?.account_id) query.account_id = params.account_id;
      if (params?.status) query.status = params.status;
      if (params?.page) query.page = String(params.page);
      const res = await apiClient.reconciliation.getReports(query);
      set({
        reports: res.data as ReconciliationReport[],
        totalReports: res.total,
        currentPage: res.page,
        totalPages: res.totalPages,
        loading: false,
      });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Failed to fetch reports', loading: false });
    }
  },

  fetchConfig: async () => {
    try {
      const res = await apiClient.reconciliation.getConfig();
      set({ config: res as ReconciliationConfig });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Failed to fetch config' });
    }
  },

  updateConfig: async (dto) => {
    try {
      const res = await apiClient.reconciliation.updateConfig(dto as Record<string, unknown>);
      set({ config: res as ReconciliationConfig });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Failed to update config' });
    }
  },

  fetchAccountStatus: async (accountId) => {
    try {
      const res = await apiClient.reconciliation.getAccountStatus(accountId);
      set((state) => ({
        accountStatuses: { ...state.accountStatuses, [accountId]: res as AccountReconciliationStatus },
      }));
    } catch {
      // silently fail
    }
  },

  subscribeToAlerts: () => {
    if (get().alertSubId) return;

    const subId = wsManager.subscribe('reconciliation_discrepancy', (payload: unknown) => {
      const data = payload as { accountId: string; discrepancies: Array<{ type: string; severity: string }> };
      set({ recentAlert: data });
    });
    set({ alertSubId: subId });
  },

  unsubscribeFromAlerts: () => {
    const subId = get().alertSubId;
    if (subId) {
      wsManager.unsubscribe(subId);
      set({ alertSubId: null });
    }
  },

  dismissAlert: () => {
    set({ recentAlert: null });
  },
}));
