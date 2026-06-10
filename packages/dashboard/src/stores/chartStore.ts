import { create } from 'zustand';
import type { Candle } from '../types/candle';
import type { Timeframe } from '../types/timeframe';
import { apiClient } from '../services/ApiClient';

interface ChartState {
  instrument: string;
  timeframe: Timeframe;
  candles: Candle[];
  loading: boolean;
  error: string | null;
  fitContentFn: (() => void) | null;
  isFullscreen: boolean;
  setInstrument: (instrument: string) => void;
  setTimeframe: (timeframe: Timeframe) => void;
  addCandle: (candle: Candle) => void;
  setCandles: (candles: Candle[]) => void;
  fetchCandles: () => Promise<void>;
  setFitContentFn: (fn: (() => void) | null) => void;
  fitContent: () => void;
  toggleFullscreen: () => void;
}

export const useChartStore = create<ChartState>((set, get) => ({
  instrument: '',
  timeframe: '1m',
  candles: [],
  loading: false,
  error: null,
  fitContentFn: null,
  isFullscreen: false,
  setInstrument: (instrument) => set({ instrument, candles: [], error: null }),
  setTimeframe: (timeframe) => set({ timeframe, candles: [], error: null }),
  setFitContentFn: (fn) => set({ fitContentFn: fn }),
  fitContent: () => {
    const fn = get().fitContentFn;
    if (fn) fn();
  },
  toggleFullscreen: () => set((s) => ({ isFullscreen: !s.isFullscreen })),
  addCandle: (candle) =>
    set((state) => {
      const existing = state.candles;
      // If the last candle has the same timestamp, update it (live tick)
      if (
        existing.length > 0 &&
        existing[existing.length - 1].timestamp === candle.timestamp
      ) {
        const updated = [...existing];
        updated[updated.length - 1] = candle;
        return { candles: updated };
      }
      return { candles: [...existing, candle] };
    }),
  setCandles: (candles) => set({ candles }),
  fetchCandles: async () => {
    const { instrument, timeframe } = get();
    if (!instrument) return;
    set({ loading: true, error: null });
    try {
      const candles = await apiClient.marketData.getCandles({
        instrument,
        timeframe,
        limit: 1500,
      });
      set({ candles, loading: false });
    } catch (err) {
      set({ loading: false, error: 'Failed to load candles' });
    }
  },
}));
