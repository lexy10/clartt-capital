import { type FC } from 'react';

interface Props {
  status: string | null;
}

const statusColors: Record<string, string> = {
  clean: '#22c55e',
  discrepancies_found: '#f59e0b',
  error: '#ef4444',
  broker_unreachable: '#ef4444',
};

const ReconciliationStatusIndicator: FC<Props> = ({ status }) => {
  const color = status ? statusColors[status] ?? '#6b7280' : '#6b7280';
  const label = status ?? 'unknown';

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '4px',
        fontSize: '12px',
        color,
      }}
      title={`Reconciliation: ${label}`}
    >
      <span
        style={{
          width: '8px',
          height: '8px',
          borderRadius: '50%',
          backgroundColor: color,
          display: 'inline-block',
        }}
      />
      {label}
    </span>
  );
};

export default ReconciliationStatusIndicator;
