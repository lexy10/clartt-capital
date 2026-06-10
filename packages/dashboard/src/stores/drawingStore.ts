import { create } from 'zustand';

export type DrawingTool =
  | 'trendline'
  | 'hline'
  | 'rectangle'
  | 'crosshair';

interface DrawingState {
  activeTool: DrawingTool | null;
  setActiveTool: (tool: DrawingTool) => void;
  clearActiveTool: () => void;
}

export const useDrawingStore = create<DrawingState>((set) => ({
  activeTool: null,

  setActiveTool: (tool) =>
    set((state) => ({
      activeTool: state.activeTool === tool ? null : tool,
    })),

  clearActiveTool: () => set({ activeTool: null }),
}));
