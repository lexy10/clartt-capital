import { type FC, useState, useMemo } from 'react';
import type { TradeDetail } from '../../types/api';

interface TradeDrillDownProps {
  trades: TradeDetail[];
  onBack: () => void;
}

type SortColumn = keyof TradeDetail;
type SortDirection = 'asc' | 'desc';

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }) + ' ' + d.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const totalMinutes = Math.floor(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours === 0) return `${minutes}m`;
  return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
}

function formatPrice(value: number): string {
  return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 5 });
}

function formatPnl(value: number): string {
  const sign = value >= 0 ? '+' : '';
  return `${sign}${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatPips(value: number): string {
  const sign = value >= 0 ? '+' : '';
  return `${sign}${value.toFixed(1)}`;
}

function pnlColor(value: number): string {
  if (value > 0) return 'var(--success)';
  if (value < 0) return 'var(--danger)';
  return 'var(--text-primary)';
}

interface ColumnDef {
  key: SortColumn;
  label: string;
  align: 'left' | 'right';
  mono: boolean;
  render: (trade: TradeDetail) => string;
  colorFn?: (trade: TradeDetail) => string;
}

const columns: ColumnDef[] = [
  { key: 'entryTime', label: 'Entry Time', align: 'left', mono: true, render: (t) => formatTimestamp(t.entryTime) },
  { key: 'exitTime', label: 'Exit Time', align: 'left', mono: true, render: (t) => formatTimestamp(t.exitTime) },
  { key: 'instrument', label: 'Instrument', align: 'left', mono: false, render: (t) => t.instrument },
  { key: 'direction', label: 'Direction', align: 'left', mono: false, render: (t) => t.direction },
  { key: 'lotSize', label: 'Lot Size', align: 'right', mono: true, render: (t) => t.lotSize.toFixed(2) },
  { key: 'entryPrice', label: 'Entry Price', align: 'right', mono: true, render: (t) => formatPrice(t.entryPrice) },
  { key: 'exitPrice', label: 'Exit Price', align: 'right', mono: true, render: (t) => formatPrice(t.exitPrice) },
  {
    key: 'pnlDollars',
    label: 'P&L ($)',
    align: 'right',
    mono: true,
    render: (t) => formatPnl(t.pnlDollars),
    colorFn: (t) => pnlColor(t.pnlDollars),
  },
  {
    key: 'pnlPips',
    label: 'P&L (pips)',
    align: 'right',
    mono: true,
    render: (t) => formatPips(t.pnlPips),
    colorFn: (t) => pnlColor(t.pnlPips),
  },
  { key: 'duration', label: 'Duration', align: 'right', mono: true, render: (t) => formatDuration(t.duration) },
];

function compareTrades(a: TradeDetail, b: TradeDetail, column: SortColumn): number {
  const av = a[column];
  const bv = b[column];
  if (typeof av === 'number' && typeof bv === 'number') return av - bv;
  return String(av).localeCompare(String(bv));
}

const TradeDrillDown: FC<TradeDrillDownProps> = ({ trades, onBack }) => {
  const [sortColumn, setSortColumn] = useState<SortColumn>('entryTime');
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');

  const sortedTrades = useMemo(() => {
    const sorted = [...trades].sort((a, b) => compareTrades(a, b, sortColumn));
    return sortDirection === 'desc' ? sorted.reverse() : sorted;
  }, [trades, sortColumn, sortDirection]);

  const handleSort = (column: SortColumn) => {
    if (column === sortColumn) {
      setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortColumn(column);
      setSortDirection('asc');
    }
  };

  const sortIndicator = (column: SortColumn): string => {
    if (column !== sortColumn) return '';
    return sortDirection === 'asc' ? ' ▲' : ' ▼';
  };

  return (
    <div>
      <button
        onClick={onBack}
        style={{
          background: 'none',
          border: '1px solid var(--text-secondary)',
          borderRadius: 'var(--radius-sm)',
          color: 'var(--text-primary)',
          fontFamily: 'var(--font-sans)',
          fontSize: 13,
          padding: '6px 14px',
          cursor: 'pointer',
          marginBottom: 16,
        }}
      >
        ← Back to Overview
      </button>

      <div style={{ overflowX: 'auto' }}>
        <table
          style={{
            width: '100%',
            borderCollapse: 'collapse',
            background: 'var(--bg-surface)',
            borderRadius: 'var(--radius-sm)',
            fontSize: 12,
          }}
        >
          <thead>
            <tr>
              {columns.map((col) => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  style={{
                    textAlign: col.align,
                    padding: '10px 8px',
                    fontFamily: 'var(--font-sans)',
                    fontWeight: 500,
                    color: 'var(--text-secondary)',
                    borderBottom: '1px solid var(--text-secondary)',
                    cursor: 'pointer',
                    whiteSpace: 'nowrap',
                    userSelect: 'none',
                  }}
                >
                  {col.label}{sortIndicator(col.key)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sortedTrades.map((trade) => (
              <tr key={trade.tradeId}>
                {columns.map((col) => (
                  <td
                    key={col.key}
                    style={{
                      textAlign: col.align,
                      padding: '8px',
                      fontFamily: col.mono ? 'var(--font-mono)' : 'var(--font-sans)',
                      color: col.colorFn ? col.colorFn(trade) : 'var(--text-primary)',
                      borderBottom: '1px solid var(--bg-surface)',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {col.render(trade)}
                  </td>
                ))}
              </tr>
            ))}
            {sortedTrades.length === 0 && (
              <tr>
                <td
                  colSpan={columns.length}
                  style={{
                    textAlign: 'center',
                    padding: 24,
                    fontFamily: 'var(--font-sans)',
                    fontSize: 13,
                    color: 'var(--text-secondary)',
                  }}
                >
                  No trades found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default TradeDrillDown;
