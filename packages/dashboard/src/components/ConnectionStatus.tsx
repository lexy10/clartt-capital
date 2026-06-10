import { type FC } from 'react';
import { useConnectionStore, type ConnectionStatus as Status } from '../stores/connectionStore';

const STATUS_CONFIG: Record<Status, { color: string; label: string }> = {
  connected: { color: 'var(--success)', label: 'Live' },
  reconnecting: { color: 'var(--warning)', label: 'Reconnecting' },
  disconnected: { color: 'var(--danger)', label: 'Offline' },
};

const ConnectionStatus: FC = () => {
  const status = useConnectionStore((s) => s.status);
  const { color, label } = STATUS_CONFIG[status];

  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        fontSize: 11,
        fontWeight: 500,
        color: 'var(--text-muted)',
      }}
      role="status"
      aria-label={`WebSocket ${label}`}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          backgroundColor: color,
          display: 'inline-block',
          boxShadow: status === 'connected' ? `0 0 6px ${color}` : undefined,
        }}
      />
      {label}
    </div>
  );
};

export default ConnectionStatus;
