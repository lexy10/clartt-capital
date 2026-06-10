import { create } from 'zustand';
import type { Watchlist, Alert, CreateWatchlistDto, UpdateWatchlistDto, CreateAlertDto } from '../types/api';
import type { AlertTriggered } from '../types/websocket';
import { apiClient } from '../services/ApiClient';

interface WatchlistState {
  watchlists: Watchlist[];
  alerts: Alert[];
  notifications: AlertTriggered[];
  loading: boolean;
  error: string | null;
  fetchWatchlists: () => Promise<void>;
  fetchAlerts: () => Promise<void>;
  createWatchlist: (dto: CreateWatchlistDto) => Promise<void>;
  updateWatchlist: (id: string, dto: UpdateWatchlistDto) => Promise<void>;
  deleteWatchlist: (id: string) => Promise<void>;
  createAlert: (dto: CreateAlertDto) => Promise<void>;
  deleteAlert: (id: string) => Promise<void>;
  addNotification: (notification: AlertTriggered) => void;
  clearNotifications: () => void;
}

export const useWatchlistStore = create<WatchlistState>((set, get) => ({
  watchlists: [],
  alerts: [],
  notifications: [],
  loading: false,
  error: null,

  fetchWatchlists: async () => {
    set({ loading: true, error: null });
    try {
      const watchlists = await apiClient.watchlists.list();
      set({ watchlists, loading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch watchlists';
      set({ error: message, loading: false });
    }
  },

  fetchAlerts: async () => {
    set({ loading: true, error: null });
    try {
      const alerts = await apiClient.alerts.list();
      set({ alerts, loading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch alerts';
      set({ error: message, loading: false });
    }
  },

  createWatchlist: async (dto: CreateWatchlistDto) => {
    set({ loading: true, error: null });
    try {
      const created = await apiClient.watchlists.create(dto);
      set({ watchlists: [...get().watchlists, created], loading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create watchlist';
      set({ error: message, loading: false });
    }
  },

  updateWatchlist: async (id: string, dto: UpdateWatchlistDto) => {
    set({ loading: true, error: null });
    try {
      const updated = await apiClient.watchlists.update(id, dto);
      set({
        watchlists: get().watchlists.map((w) => (w.id === id ? updated : w)),
        loading: false,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update watchlist';
      set({ error: message, loading: false });
    }
  },

  deleteWatchlist: async (id: string) => {
    set({ loading: true, error: null });
    try {
      await apiClient.watchlists.delete(id);
      set({ watchlists: get().watchlists.filter((w) => w.id !== id), loading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to delete watchlist';
      set({ error: message, loading: false });
    }
  },

  createAlert: async (dto: CreateAlertDto) => {
    set({ loading: true, error: null });
    try {
      const created = await apiClient.alerts.create(dto);
      set({ alerts: [...get().alerts, created], loading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create alert';
      set({ error: message, loading: false });
    }
  },

  deleteAlert: async (id: string) => {
    set({ loading: true, error: null });
    try {
      await apiClient.alerts.delete(id);
      set({ alerts: get().alerts.filter((a) => a.id !== id), loading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to delete alert';
      set({ error: message, loading: false });
    }
  },

  addNotification: (notification: AlertTriggered) => {
    set({ notifications: [notification, ...get().notifications].slice(0, 50) });
  },

  clearNotifications: () => set({ notifications: [] }),
}));
