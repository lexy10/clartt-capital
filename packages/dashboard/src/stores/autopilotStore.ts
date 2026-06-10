import { create } from 'zustand';
import type {
  EntryZone,
  ExitZone,
  OrderBlockZone,
  TradeMarker,
  OverlayData,
} from '../types/autopilot';

type OverlayType = 'entryZones' | 'exitZones' | 'orderBlocks';

interface AutopilotStoreState {
  enabled: boolean;
  overlays: {
    entryZones: EntryZone[];
    exitZones: ExitZone[];
    orderBlocks: OrderBlockZone[];
  };
  tradeMarkers: TradeMarker[];
  overlayVisibility: {
    entryZones: boolean;
    exitZones: boolean;
    orderBlocks: boolean;
  };
  sessionPnL: number;
  openPositionCount: number;
  strategyName: string;

  setEnabled: (enabled: boolean) => void;
  toggleOverlayVisibility: (type: OverlayType) => void;
  addOverlay: (overlay: OverlayData) => void;
  addTradeMarker: (marker: TradeMarker) => void;
  updateSessionStats: (stats: {
    sessionPnL?: number;
    openPositionCount?: number;
    strategyName?: string;
  }) => void;
}

export const useAutopilotStore = create<AutopilotStoreState>((set) => ({
  enabled: false,
  overlays: {
    entryZones: [],
    exitZones: [],
    orderBlocks: [],
  },
  tradeMarkers: [],
  overlayVisibility: {
    entryZones: true,
    exitZones: true,
    orderBlocks: true,
  },
  sessionPnL: 0,
  openPositionCount: 0,
  strategyName: '',

  setEnabled: (enabled) => set({ enabled }),

  toggleOverlayVisibility: (type) =>
    set((state) => ({
      overlayVisibility: {
        ...state.overlayVisibility,
        [type]: !state.overlayVisibility[type],
      },
    })),

  addOverlay: (overlay) =>
    set((state) => {
      const overlays = { ...state.overlays };
      switch (overlay.kind) {
        case 'entry_zone': {
          const { kind: _, ...zone } = overlay;
          overlays.entryZones = [...overlays.entryZones, zone];
          break;
        }
        case 'exit_zone': {
          const { kind: _, ...zone } = overlay;
          overlays.exitZones = [...overlays.exitZones, zone];
          break;
        }
        case 'order_block': {
          const { kind: _, ...zone } = overlay;
          overlays.orderBlocks = [...overlays.orderBlocks, zone];
          break;
        }
      }
      return { overlays };
    }),

  addTradeMarker: (marker) =>
    set((state) => ({
      tradeMarkers: [...state.tradeMarkers, marker],
    })),

  updateSessionStats: (stats) =>
    set(() => ({
      ...(stats.sessionPnL !== undefined && { sessionPnL: stats.sessionPnL }),
      ...(stats.openPositionCount !== undefined && {
        openPositionCount: stats.openPositionCount,
      }),
      ...(stats.strategyName !== undefined && {
        strategyName: stats.strategyName,
      }),
    })),
}));
