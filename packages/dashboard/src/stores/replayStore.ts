import { create } from 'zustand';
import type { Candle } from '../types/candle';
import { useChartStore } from './chartStore';

export type ReplayStatus = 'idle' | 'playing' | 'paused';

interface ReplayState {
  status: ReplayStatus;
  speed: number;
  currentIndex: number;
  historicalCandles: Candle[];
  intervalId: ReturnType<typeof setInterval> | null;

  loadCandles: (candles: Candle[]) => void;
  play: () => void;
  pause: () => void;
  resume: () => void;
  rewind: () => void;
  step: () => void;
  setSpeed: (speed: number) => void;
  stop: () => void;
}

function emitCandle(state: ReplayState): number {
  const { historicalCandles, currentIndex } = state;
  if (currentIndex >= historicalCandles.length) return currentIndex;
  const candle = historicalCandles[currentIndex];
  useChartStore.getState().addCandle(candle);
  return currentIndex + 1;
}

function clearTimer(state: ReplayState) {
  if (state.intervalId !== null) {
    clearInterval(state.intervalId);
  }
}

function startTimer(set: (fn: (s: ReplayState) => Partial<ReplayState>) => void, get: () => ReplayState) {
  const { speed, historicalCandles } = get();
  const intervalMs = Math.max(10, 1000 / speed);

  const id = setInterval(() => {
    const s = get();
    if (s.status !== 'playing') return;
    if (s.currentIndex >= historicalCandles.length) {
      clearTimer(s);
      set(() => ({ status: 'paused', intervalId: null }));
      return;
    }
    const nextIndex = emitCandle(s);
    set(() => ({ currentIndex: nextIndex }));
  }, intervalMs);

  return id;
}

export const useReplayStore = create<ReplayState>((set, get) => ({
  status: 'idle',
  speed: 1,
  currentIndex: 0,
  historicalCandles: [],
  intervalId: null,

  loadCandles: (candles: Candle[]) => {
    const state = get();
    clearTimer(state);
    // Sort candles in strictly ascending chronological order
    const sorted = [...candles].sort(
      (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
    );
    useChartStore.getState().setCandles([]);
    set({ historicalCandles: sorted, currentIndex: 0, status: 'idle', intervalId: null });
  },

  play: () => {
    const state = get();
    if (state.historicalCandles.length === 0) return;
    clearTimer(state);
    // Reset chart and index when playing from idle
    if (state.status === 'idle') {
      useChartStore.getState().setCandles([]);
      set({ currentIndex: 0 });
    }
    set({ status: 'playing' });
    const id = startTimer(set, get);
    set({ intervalId: id });
  },

  pause: () => {
    const state = get();
    clearTimer(state);
    set({ status: 'paused', intervalId: null });
  },

  resume: () => {
    const state = get();
    if (state.status !== 'paused') return;
    set({ status: 'playing' });
    const id = startTimer(set, get);
    set({ intervalId: id });
  },

  rewind: () => {
    const state = get();
    clearTimer(state);
    useChartStore.getState().setCandles([]);
    set({ currentIndex: 0, status: 'idle', intervalId: null });
  },

  step: () => {
    const state = get();
    if (state.currentIndex >= state.historicalCandles.length) return;
    // Pause if playing
    clearTimer(state);
    const nextIndex = emitCandle(state);
    set({ currentIndex: nextIndex, status: 'paused', intervalId: null });
  },

  setSpeed: (speed: number) => {
    const clamped = Math.max(1, Math.min(100, speed));
    const state = get();
    set({ speed: clamped });
    // If currently playing, restart the timer with new speed
    if (state.status === 'playing') {
      clearTimer(state);
      const id = startTimer(set, get);
      set({ intervalId: id });
    }
  },

  stop: () => {
    const state = get();
    clearTimer(state);
    set({ status: 'idle', currentIndex: 0, intervalId: null });
  },
}));
