import { type FC } from 'react';
import { useDrawingStore, type DrawingTool } from '../stores/drawingStore';

const TOOLS: { id: DrawingTool; label: string; icon: string }[] = [
  { id: 'trendline', label: 'Trend Line', icon: '╲' },
  { id: 'hline', label: 'Horizontal Line', icon: '─' },
  { id: 'rectangle', label: 'Rectangle', icon: '▭' },
  { id: 'crosshair', label: 'Crosshair', icon: '┼' },
];

const DrawingToolbar: FC = () => {
  const activeTool = useDrawingStore((s) => s.activeTool);
  const setActiveTool = useDrawingStore((s) => s.setActiveTool);
  const clearActiveTool = useDrawingStore((s) => s.clearActiveTool);

  return (
    <div className="drawing-toolbar" role="toolbar" aria-label="Drawing tools">
      {TOOLS.map(({ id, label, icon }) => (
        <button
          key={id}
          className={`drawing-btn${activeTool === id ? ' drawing-btn-active' : ''}`}
          onClick={() => setActiveTool(id)}
          aria-pressed={activeTool === id}
          aria-label={label}
          title={label}
        >
          <span className="drawing-icon mono">{icon}</span>
        </button>
      ))}
      {activeTool && (
        <button
          className="drawing-btn drawing-btn-clear"
          onClick={clearActiveTool}
          aria-label="Clear drawing tool"
          title="Clear selection"
        >
          <span className="drawing-icon">✕</span>
        </button>
      )}
    </div>
  );
};

export default DrawingToolbar;
