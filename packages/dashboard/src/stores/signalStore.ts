import { create } from 'zustand';
import type { Signal, SignalDirection, SignalMode, RawSignal } from '../types/signal';
import { normalizeSignal } from '../types/signal';
import type { Timeframe } from '../types/timeframe';
import { apiClient } from '../services/ApiClient';

export interface SignalFilters {
  direction: SignalDirection | 'ALL';
  mode: SignalMode | 'ALL';
  timeframe: Timeframe | 'ALL';
}

export interface SignalSort {
  field: 'created_at' | 'confidence_score' | 'entry_price';
  order: 'asc' | 'desc';
}

interface SignalState {
  signals: Signal[];
  filteredSignals: Signal[];
  loading: boolean;
  error: string | null;
  total: number;
  offset: number;
  limit: number;
  filters: SignalFilters;
  sortBy: SignalSort;
  selectedSignal: Signal | null;

  fetchSignals: () => Promise<void>;
  setFilter: <K extends keyof SignalFilters>(key: K, value: SignalFilters[K]) => void;
  setSort: (field: SignalSort['field']) => void;
  nextPage: () => Promise<void>;
  prevPage: () => Promise<void>;
  addSignal: (signal: Signal) => void;
  selectSignal: (signal: Signal | null) => void;
}

export function applyFilters(signals: Signal[], filters: SignalFilters): Signal[] {
  return signals.filter((s) => {
    if (filters.direction !== 'ALL' && s.direction !== filters.direction) return false;
    if (filters.mode !== 'ALL' && s.mode !== filters.mode) return false;
    if (filters.timeframe !== 'ALL' && s.timeframe !== filters.timeframe) return false;
    return true;
  });
}

export function applySorting(signals: Signal[], sort: SignalSort): Signal[] {
  const sorted = [...signals];
  const { field, order } = sort;
  sorted.sort((a, b) => {
    let cmp: number;
    if (field === 'created_at') {
      cmp = a.created_at.localeCompare(b.created_at);
    } else {
      cmp = a[field] - b[field];
    }
    return order === 'asc' ? cmp : -cmp;
  });
  return sorted;
}

function recompute(signals: Signal[], filters: SignalFilters, sort: SignalSort): Signal[] {
  return applySorting(applyFilters(signals, filters), sort);
}

export const useSignalStore = create<SignalState>((set, get) => ({
  signals: [],
  filteredSignals: [],
  loading: false,
  error: null,
  total: 0,
  offset: 0,
  limit: 50,
  filters: { direction: 'ALL', mode: 'ALL', timeframe: 'ALL' },
  sortBy: { field: 'created_at', order: 'desc' },
  selectedSignal: null,

  fetchSignals: async () => {
    const { limit, offset, filters, sortBy } = get();
    set({ loading: true, error: null });
    try {
      const res = await apiClient.signals.getPaginated({ limit, offset });
      const normalized = (res.data as unknown as RawSignal[]).map(normalizeSignal);
      const filteredSignals = recompute(normalized, filters, sortBy);
      set({ signals: normalized, total: res.total, filteredSignals, loading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch signals';
      set({ error: message, loading: false });
    }
  },

  setFilter: (key, value) => {
    const { signals, filters, sortBy } = get();
    const newFilters = { ...filters, [key]: value };
    const filteredSignals = recompute(signals, newFilters, sortBy);
    set({ filters: newFilters, filteredSignals });
  },

  setSort: (field) => {
    const { signals, filters, sortBy } = get();
    const order = sortBy.field === field && sortBy.order === 'asc' ? 'desc' : 'asc';
    const newSort: SignalSort = { field, order };
    const filteredSignals = recompute(signals, filters, newSort);
    set({ sortBy: newSort, filteredSignals });
  },

  nextPage: async () => {
    const { offset, limit, total } = get();
    if (offset + limit >= total) return;
    set({ offset: offset + limit });
    await get().fetchSignals();
  },

  prevPage: async () => {
    const { offset, limit } = get();
    if (offset === 0) return;
    set({ offset: Math.max(0, offset - limit) });
    await get().fetchSignals();
  },

  addSignal: (signal) => {
    const { signals, filters, sortBy } = get();
    const normalized = normalizeSignal(signal as unknown as RawSignal);
    const newSignals = [normalized, ...signals];
    const filteredSignals = recompute(newSignals, filters, sortBy);
    set({ signals: newSignals, filteredSignals });
  },

  selectSignal: (signal) => {
    set({ selectedSignal: signal });
  },
}));
