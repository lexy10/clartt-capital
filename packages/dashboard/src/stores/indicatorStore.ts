import { create } from 'zustand';

export type IndicatorType = 'MA' | 'RSI' | 'MACD' | 'BB';

export interface IndicatorConfig {
  enabled: boolean;
  params: Record<string, number>;
}

const DEFAULT_CONFIGS: Record<IndicatorType, IndicatorConfig> = {
  MA: { enabled: false, params: { period: 20 } },
  RSI: { enabled: false, params: { period: 14 } },
  MACD: { enabled: false, params: { fast: 12, slow: 26, signal: 9 } },
  BB: { enabled: false, params: { period: 20, stdDev: 2 } },
};

export interface CompareInstrument {
  symbol: string;
  color: string;
}

interface IndicatorState {
  indicators: Record<IndicatorType, IndicatorConfig>;
  compareInstruments: CompareInstrument[];
  toggleIndicator: (type: IndicatorType) => void;
  setIndicatorParam: (type: IndicatorType, key: string, value: number) => void;
  addCompareInstrument: (symbol: string, color: string) => void;
  removeCompareInstrument: (symbol: string) => void;
}

export const useIndicatorStore = create<IndicatorState>((set) => ({
  indicators: { ...DEFAULT_CONFIGS },
  compareInstruments: [],

  toggleIndicator: (type) =>
    set((state) => ({
      indicators: {
        ...state.indicators,
        [type]: {
          ...state.indicators[type],
          enabled: !state.indicators[type].enabled,
        },
      },
    })),

  setIndicatorParam: (type, key, value) =>
    set((state) => ({
      indicators: {
        ...state.indicators,
        [type]: {
          ...state.indicators[type],
          params: { ...state.indicators[type].params, [key]: value },
        },
      },
    })),

  addCompareInstrument: (symbol, color) =>
    set((state) => {
      if (state.compareInstruments.some((c) => c.symbol === symbol)) {
        return state;
      }
      return {
        compareInstruments: [...state.compareInstruments, { symbol, color }],
      };
    }),

  removeCompareInstrument: (symbol) =>
    set((state) => ({
      compareInstruments: state.compareInstruments.filter(
        (c) => c.symbol !== symbol
      ),
    })),
}));
