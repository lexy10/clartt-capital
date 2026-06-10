import { create } from 'zustand';
import type { Position } from '../types/trade';
import { apiClient } from '../services/ApiClient';

interface PortfolioState {
  balance: number;
  equity: number;
  unrealizedPnl: number;
  positions: Position[];
  accounts: { id: string; label: string }[];
  loading: boolean;
  error: string | null;
  fetchSummary: () => Promise<void>;
  fetchPositions: () => Promise<void>;
  setPositions: (positions: Position[]) => void;
}

export const usePortfolioStore = create<PortfolioState>((set) => ({
  balance: 0,
  equity: 0,
  unrealizedPnl: 0,
  positions: [],
  accounts: [],
  loading: false,
  error: null,

  fetchSummary: async () => {
    set({ loading: true, error: null });
    try {
      const summary: any = await apiClient.portfolios.getSummary();
      set({
        balance: summary.balance ?? 0,
        equity: summary.equity ?? 0,
        unrealizedPnl: summary.unrealized_pnl ?? summary.totalUnrealizedPnl ?? 0,
        accounts: summary.accounts ?? [],
        loading: false,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch portfolio summary';
      set({ error: message, loading: false });
    }
  },

  fetchPositions: async () => {
    set({ loading: true, error: null });
    try {
      const positions = await apiClient.portfolios.getPositions();
      set({ positions, loading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch positions';
      set({ error: message, loading: false });
    }
  },

  setPositions: (positions) => set({ positions }),
}));
