import { type FC, useCallback } from 'react';
import { useAutopilotStore } from '../stores/autopilotStore';

type OverlayType = 'entryZones' | 'exitZones' | 'orderBlocks';

const overlayConfig: { type: OverlayType; label: string; color: string }[] = [
  { type: 'entryZones', label: 'Entry Zones', color: 'var(--success)' },
  { type: 'exitZones', label: 'Exit Zones', color: 'var(--danger)' },
  { type: 'orderBlocks', label: 'Order Blocks', color: 'var(--accent)' },
];

const StrategyOverlayPanel: FC = () => {
  const overlayVisibility = useAutopilotStore((s) => s.overlayVisibility);
  const toggleOverlayVisibility = useAutopilotStore((s) => s.toggleOverlayVisibility);

  const handleToggle = useCallback(
    (type: OverlayType) => () => { toggleOverlayVisibility(type); },
    [toggleOverlayVisibility],
  );

  return (
    <div className="card" role="region" aria-label="Strategy overlay controls">
      <div className="card-header">
        <span className="card-title">Overlays</span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {overlayConfig.map(({ type, label, color }) => {
          const active = overlayVisibility[type];
          return (
            <div key={type} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  background: color,
                  flexShrink: 0,
                }} />
                <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-secondary)' }}>{label}</span>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={active}
                aria-label={`Toggle ${label}`}
                onClick={handleToggle(type)}
                style={{
                  position: 'relative',
                  width: 32,
                  height: 18,
                  borderRadius: 9,
                  background: active ? 'var(--accent)' : 'var(--bg-surface)',
                  border: `1px solid ${active ? 'var(--accent)' : 'var(--border-primary)'}`,
                  cursor: 'pointer',
                  transition: 'all 250ms cubic-bezier(0.4, 0, 0.2, 1)',
                  flexShrink: 0,
                }}
              >
                <span style={{
                  position: 'absolute',
                  top: 2,
                  left: active ? 16 : 2,
                  width: 12,
                  height: 12,
                  borderRadius: '50%',
                  background: active ? '#fff' : 'var(--text-muted)',
                  transition: 'all 250ms cubic-bezier(0.4, 0, 0.2, 1)',
                  boxShadow: '0 1px 2px rgba(0,0,0,0.3)',
                }} />
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default StrategyOverlayPanel;
