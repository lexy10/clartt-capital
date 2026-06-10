import { create } from 'zustand';
import type { TradingAccount, AccountDetails, CreateAccountDto } from '../types/trading-account';
import { apiClient } from '../services/ApiClient';
import { wsManager } from '../services/WebSocketManager';

interface AccountState {
  accounts: TradingAccount[];
  accountDetails: Record<string, AccountDetails>;
  accountStatuses: Record<string, { state: string; connection_status: string }>;
  brokerSymbols: Record<string, string[]>;
  loading: boolean;
  error: string | null;
  fetchAccounts: (silent?: boolean) => Promise<void>;
  addAccount: (dto: CreateAccountDto) => Promise<void>;
  fetchDetails: (id: string) => Promise<void>;
  fetchStatus: (id: string) => Promise<{ state: string; connection_status: string } | null>;
  updateLabel: (id: string, label: string) => Promise<void>;
  removeAccount: (id: string) => Promise<void>;
  deployAccount: (id: string) => Promise<void>;
  undeployAccount: (id: string) => Promise<void>;
  fetchBrokerSymbols: (accountId: string) => Promise<void>;
  subscribeToSync: () => void;
  unsubscribeFromSync: () => void;
}

export const useAccountStore = create<AccountState>((set) => ({
  accounts: [],
  accountDetails: {},
  accountStatuses: {},
  brokerSymbols: {},
  loading: false,
  error: null,

  fetchAccounts: async (silent = false) => {
    if (!silent) set({ loading: true, error: null });
    else set({ error: null });
    try {
      const accounts = await apiClient.accounts.list();
      set({ accounts, loading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch accounts';
      set({ error: message, loading: false });
    }
  },

  addAccount: async (dto: CreateAccountDto) => {
    set({ loading: true, error: null });
    try {
      const account = await apiClient.accounts.create(dto);
      set((state) => ({
        accounts: [...state.accounts, account],
        loading: false,
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to add account';
      set({ error: message, loading: false });
    }
  },

  fetchDetails: async (id: string) => {
    set({ error: null });
    try {
      const details = await apiClient.accounts.getDetails(id);
      set((state) => ({
        accountDetails: { ...state.accountDetails, [id]: details },
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch account details';
      set({ error: message });
    }
  },

  fetchStatus: async (id: string) => {
    try {
      const status = await apiClient.accounts.getStatus(id);
      set((state) => ({
        accountStatuses: { ...state.accountStatuses, [id]: status },
      }));
      return status;
    } catch {
      return null;
    }
  },

  updateLabel: async (id: string, label: string) => {
    set({ error: null });
    try {
      const updated = await apiClient.accounts.updateLabel(id, label);
      set((state) => ({
        accounts: state.accounts.map((a) => (a.id === id ? updated : a)),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update label';
      set({ error: message });
    }
  },

  removeAccount: async (id: string) => {
    set({ loading: true, error: null });
    try {
      await apiClient.accounts.remove(id);
      set((state) => ({
        accounts: state.accounts.filter((a) => a.id !== id),
        accountDetails: Object.fromEntries(
          Object.entries(state.accountDetails).filter(([key]) => key !== id),
        ),
        loading: false,
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to remove account';
      set({ error: message, loading: false });
    }
  },

  fetchBrokerSymbols: async (accountId: string) => {
    try {
      const symbols = await apiClient.accounts.getBrokerSymbols(accountId);
      set((state) => ({
        brokerSymbols: { ...state.brokerSymbols, [accountId]: symbols },
      }));
    } catch (err) {
      console.warn('Failed to fetch broker symbols:', err);
    }
  },

  deployAccount: async (id: string) => {
    set({ error: null });
    try {
      await apiClient.accounts.deploy(id);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to deploy account';
      set({ error: message });
    }
  },

  undeployAccount: async (id: string) => {
    set({ error: null });
    try {
      await apiClient.accounts.undeploy(id);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to undeploy account';
      set({ error: message });
    }
  },

  subscribeToSync: () => {
    const subId = wsManager.subscribe('account_sync', () => {
      // Silent — sync events fire on every tick; never flash skeletons here.
      const store = useAccountStore.getState();
      store.fetchAccounts(true).then(() => {
        const accounts = useAccountStore.getState().accounts;
        for (const acc of accounts) {
          store.fetchDetails(acc.id);
        }
      });
    });
    (useAccountStore as unknown as { _syncSubId?: string })._syncSubId = subId;
  },

  unsubscribeFromSync: () => {
    const subId = (useAccountStore as unknown as { _syncSubId?: string })._syncSubId;
    if (subId) {
      wsManager.unsubscribe(subId);
    }
  },
}));
