import { create } from 'zustand';

export interface SandboxPosition {
  id: string;
  instrument: string;
  direction: 'BUY' | 'SELL';
  size: number;
  entryPrice: number;
  currentPrice: number;
  openedAt: string;
}

export interface PlaceTradeParams {
  instrument: string;
  direction: 'BUY' | 'SELL';
  size: number;
  entryPrice: number;
}

function computePnl(pos: SandboxPosition): number {
  const diff = pos.currentPrice - pos.entryPrice;
  return pos.direction === 'BUY' ? diff * pos.size : -diff * pos.size;
}

interface SandboxState {
  enabled: boolean;
  virtualBalance: number;
  positions: SandboxPosition[];

  toggleSandbox: () => void;
  placeTrade: (params: PlaceTradeParams) => void;
  closePosition: (id: string) => void;
  updatePrices: (priceMap: Record<string, number>) => void;

  /** Derived helpers */
  totalUnrealizedPnl: () => number;
  portfolioValue: () => number;
}

const INITIAL_BALANCE = 100_000;
let nextId = 1;

export const useSandboxStore = create<SandboxState>((set, get) => ({
  enabled: false,
  virtualBalance: INITIAL_BALANCE,
  positions: [],

  toggleSandbox: () =>
    set((s) => ({
      enabled: !s.enabled,
      // Reset state when toggling off
      ...(!s.enabled
        ? {}
        : { virtualBalance: INITIAL_BALANCE, positions: [] }),
    })),

  placeTrade: (params) => {
    const position: SandboxPosition = {
      id: `sandbox-${nextId++}`,
      instrument: params.instrument,
      direction: params.direction,
      size: params.size,
      entryPrice: params.entryPrice,
      currentPrice: params.entryPrice,
      openedAt: new Date().toISOString(),
    };
    set((s) => ({ positions: [...s.positions, position] }));
  },

  closePosition: (id) =>
    set((s) => {
      const pos = s.positions.find((p) => p.id === id);
      if (!pos) return s;
      const pnl = computePnl(pos);
      return {
        positions: s.positions.filter((p) => p.id !== id),
        virtualBalance: s.virtualBalance + pnl,
      };
    }),

  updatePrices: (priceMap) =>
    set((s) => ({
      positions: s.positions.map((p) =>
        priceMap[p.instrument] !== undefined
          ? { ...p, currentPrice: priceMap[p.instrument] }
          : p,
      ),
    })),

  totalUnrealizedPnl: () =>
    get().positions.reduce((sum, p) => sum + computePnl(p), 0),

  portfolioValue: () =>
    get().virtualBalance +
    get().positions.reduce((sum, p) => sum + computePnl(p), 0),
}));
