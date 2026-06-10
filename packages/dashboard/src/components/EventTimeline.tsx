import { type FC, type ReactNode, useEffect, useState } from 'react';
import { useEventStore, subscribeEventUpdates, unsubscribeEventUpdates } from '../stores/eventStore';
import { TradingEventType, type TradingEvent } from '../types/event';

/* ── Color mapping (exported for testing) ── */

export function getEventColor(eventType: string): string {
  switch (eventType) {
    case 'TradeExecuted':
    case 'PositionOpened':
      return '#22c55e'; // green
    case 'TradeFailed':
    case 'KillSwitchActivated':
      return '#ef4444'; // red
    case 'RiskEvaluated':
    case 'SignalGenerated':
      return '#eab308'; // yellow
    case 'AutopilotStateChanged':
    case 'PositionUpdated':
      return '#3b82f6'; // blue
    default:
      return '#6b7280'; // gray
  }
}

/* ── Helpers ── */

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return (
    d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
    ' ' +
    d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  );
}

function payloadSummary(payload: Record<string, unknown>): string {
  const parts: string[] = [];
  if (payload.instrument) parts.push(String(payload.instrument));
  if (payload.direction) parts.push(String(payload.direction));
  if (payload.account_id) parts.push(`acct:${String(payload.account_id).slice(0, 8)}`);
  if (payload.signal_id) parts.push(`sig:${String(payload.signal_id).slice(0, 8)}`);
  if (payload.failure_reason) parts.push(String(payload.failure_reason));
  if (payload.reason) parts.push(String(payload.reason));
  if (payload.scope) parts.push(String(payload.scope));
  if (parts.length === 0) return JSON.stringify(payload).slice(0, 80);
  return parts.join(' · ');
}

/** Group events by aggregate_id preserving order of first appearance */
function groupByAggregate(events: TradingEvent[]): Map<string, TradingEvent[]> {
  const map = new Map<string, TradingEvent[]>();
  for (const ev of events) {
    const group = map.get(ev.aggregateId);
    if (group) group.push(ev);
    else map.set(ev.aggregateId, [ev]);
  }
  return map;
}

const EVENT_TYPE_OPTIONS = ['', ...Object.values(TradingEventType)];

/* ── Component ── */

const EventTimeline: FC = () => {
  const {
    events, filters, loading, totalCount, currentPage, totalPages, selectedEvent,
    fetchEvents, setFilters, selectEvent,
  } = useEventStore();

  const [searchType, setSearchType] = useState<'aggregate_id' | 'correlation_id'>('aggregate_id');
  const [searchValue, setSearchValue] = useState('');

  // Fetch on mount + subscribe to WebSocket
  useEffect(() => {
    fetchEvents();
    subscribeEventUpdates();
    return () => unsubscribeEventUpdates();
  }, [fetchEvents]);

  const handleSearch = () => {
    if (!searchValue.trim()) return;
    setFilters({ [searchType]: searchValue.trim(), page: 1 });
  };

  const handleClearFilters = () => {
    setSearchValue('');
    setFilters({
      account_id: undefined,
      instrument: undefined,
      event_type: undefined,
      start_time: undefined,
      end_time: undefined,
      aggregate_id: undefined,
      correlation_id: undefined,
      page: 1,
    });
  };

  const grouped = groupByAggregate(events);
  const prevDisabled = currentPage <= 1;
  const nextDisabled = currentPage >= totalPages;

  /* ── Detail panel ── */
  if (selectedEvent) {
    return (
      <div style={pageStyle}>
        <button onClick={() => selectEvent(null)} style={backBtnStyle} aria-label="Close detail panel">
          ← Back to Timeline
        </button>

        <h2 style={{ fontSize: 18, fontWeight: 600, margin: '16px 0 20px' }}>Event Detail</h2>

        <div style={{ background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)', padding: 20 }}>
          <DetailRow label="Event Type">
            <span style={{ color: getEventColor(selectedEvent.eventType), fontWeight: 600, fontSize: 12 }}>
              {selectedEvent.eventType}
            </span>
          </DetailRow>
          <DetailRow label="ID" value={selectedEvent.id} mono />
          <DetailRow label="Aggregate ID" value={selectedEvent.aggregateId} mono />
          <DetailRow label="Sequence #" value={String(selectedEvent.sequenceNumber)} mono />
          <DetailRow label="Correlation ID" value={selectedEvent.correlationId ?? '—'} mono />
          <DetailRow label="Source Service" value={selectedEvent.sourceService} />
          <DetailRow label="Created At" value={fmtDate(selectedEvent.createdAt)} mono />
          <DetailRow label="Schema Version" value={String(selectedEvent.schemaVersion)} />

          <h3 style={sectionHeadingStyle}>Payload</h3>
          <pre style={jsonPreStyle}>{JSON.stringify(selectedEvent.payload, null, 2)}</pre>

          {selectedEvent.contextSnapshot && (
            <>
              <h3 style={sectionHeadingStyle}>Context Snapshot</h3>
              <pre style={jsonPreStyle}>{JSON.stringify(selectedEvent.contextSnapshot, null, 2)}</pre>
            </>
          )}
        </div>
      </div>
    );
  }

  /* ── Main timeline view ── */
  return (
    <div style={pageStyle}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>Event Timeline</h1>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{totalCount} events</span>
      </div>

      {/* Search bar */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <select
          value={searchType}
          onChange={(e) => setSearchType(e.target.value as 'aggregate_id' | 'correlation_id')}
          style={selectStyle}
          aria-label="Search type"
        >
          <option value="aggregate_id">Aggregate ID</option>
          <option value="correlation_id">Correlation ID</option>
        </select>
        <input
          type="text"
          value={searchValue}
          onChange={(e) => setSearchValue(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          placeholder="Search by ID…"
          style={inputStyle}
          aria-label="Search value"
        />
        <button onClick={handleSearch} style={actionBtnStyle}>Search</button>
        <button onClick={handleClearFilters} style={{ ...actionBtnStyle, background: 'transparent', color: 'var(--text-secondary)' }}>
          Clear
        </button>
      </div>

      {/* Filter controls */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        <FilterInput
          label="Account"
          value={filters.account_id ?? ''}
          onChange={(v) => setFilters({ account_id: v || undefined, page: 1 })}
        />
        <FilterInput
          label="Instrument"
          value={filters.instrument ?? ''}
          onChange={(v) => setFilters({ instrument: v || undefined, page: 1 })}
        />
        <FilterSelect
          label="Event Type"
          value={filters.event_type ?? ''}
          options={EVENT_TYPE_OPTIONS}
          onChange={(v) => setFilters({ event_type: (v || undefined) as TradingEventType | undefined, page: 1 })}
        />
        <FilterInput
          label="Start"
          value={filters.start_time ?? ''}
          onChange={(v) => setFilters({ start_time: v || undefined, page: 1 })}
          placeholder="YYYY-MM-DD"
          type="date"
        />
        <FilterInput
          label="End"
          value={filters.end_time ?? ''}
          onChange={(v) => setFilters({ end_time: v || undefined, page: 1 })}
          placeholder="YYYY-MM-DD"
          type="date"
        />
      </div>

      {/* Loading */}
      {loading && events.length === 0 && (
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', padding: 16 }}>Loading events…</div>
      )}

      {/* Empty */}
      {!loading && events.length === 0 && (
        <div style={emptyStyle}>No events found</div>
      )}

      {/* Timeline grouped by aggregate */}
      {events.length > 0 && (
        <div>
          {Array.from(grouped.entries()).map(([aggId, aggEvents]) => (
            <div key={aggId} style={{ marginBottom: 20 }}>
              <div style={aggHeaderStyle}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>{aggId}</span>
                <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{aggEvents.length} event{aggEvents.length !== 1 ? 's' : ''}</span>
              </div>
              {aggEvents.map((ev) => (
                <div
                  key={ev.id}
                  onClick={() => selectEvent(ev)}
                  style={timelineItemStyle}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => e.key === 'Enter' && selectEvent(ev)}
                  aria-label={`Event ${ev.eventType} at ${fmtDate(ev.createdAt)}`}
                >
                  {/* Color dot */}
                  <div style={{ ...dotStyle, background: getEventColor(ev.eventType) }} />
                  {/* Content */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontSize: 12, fontWeight: 600, color: getEventColor(ev.eventType) }}>
                        {ev.eventType}
                      </span>
                      <span style={{ fontSize: 11, color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' }}>
                        {fmtDate(ev.createdAt)}
                      </span>
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>
                      {ev.sourceService} · {payloadSummary(ev.payload)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div style={{ display: 'flex', justifyContent: 'center', gap: 12, marginTop: 16 }}>
          <button onClick={() => setFilters({ page: currentPage - 1 })} disabled={prevDisabled} style={paginationBtnStyle(prevDisabled)}>
            ← Prev
          </button>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: '32px' }}>
            Page {currentPage} of {totalPages}
          </span>
          <button onClick={() => setFilters({ page: currentPage + 1 })} disabled={nextDisabled} style={paginationBtnStyle(nextDisabled)}>
            Next →
          </button>
        </div>
      )}
    </div>
  );
};

/* ── Sub-components ── */

const DetailRow: FC<{ label: string; value?: string; mono?: boolean; children?: ReactNode }> = ({ label, value, mono, children }) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid var(--border-primary)' }}>
    <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{label}</span>
    {children ?? (
      <span style={{ fontSize: 12, fontFamily: mono ? 'var(--font-mono)' : 'var(--font-sans)', color: 'var(--text-primary)', wordBreak: 'break-all', textAlign: 'right', maxWidth: '60%' }}>
        {value}
      </span>
    )}
  </div>
);

const FilterSelect: FC<{ label: string; value: string; options: string[]; onChange: (v: string) => void }> = ({ label, value, options, onChange }) => (
  <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--text-secondary)' }}>
    {label}:
    <select value={value} onChange={(e) => onChange(e.target.value)} style={selectStyle} aria-label={`Filter by ${label}`}>
      {options.map((o) => (
        <option key={o} value={o}>{o || 'All'}</option>
      ))}
    </select>
  </label>
);

const FilterInput: FC<{ label: string; value: string; onChange: (v: string) => void; placeholder?: string; type?: string }> = ({
  label, value, onChange, placeholder, type = 'text',
}) => (
  <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--text-secondary)' }}>
    {label}:
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      style={inputStyle}
      aria-label={`Filter by ${label}`}
    />
  </label>
);

/* ── Styles ── */

const pageStyle: React.CSSProperties = {
  padding: 24,
  fontFamily: 'var(--font-sans)',
  color: 'var(--text-primary)',
  background: 'var(--bg-primary)',
  minHeight: '100%',
};

const backBtnStyle: React.CSSProperties = {
  background: 'none',
  border: '1px solid var(--border-primary)',
  borderRadius: 'var(--radius-sm)',
  color: 'var(--text-secondary)',
  padding: '6px 12px',
  fontSize: 12,
  cursor: 'pointer',
};

const selectStyle: React.CSSProperties = {
  background: 'var(--bg-surface)',
  color: 'var(--text-primary)',
  border: '1px solid var(--border-primary)',
  borderRadius: 'var(--radius-sm)',
  padding: '4px 8px',
  fontSize: 12,
  cursor: 'pointer',
};

const inputStyle: React.CSSProperties = {
  background: 'var(--bg-surface)',
  color: 'var(--text-primary)',
  border: '1px solid var(--border-primary)',
  borderRadius: 'var(--radius-sm)',
  padding: '4px 8px',
  fontSize: 12,
  width: 140,
};

const actionBtnStyle: React.CSSProperties = {
  background: 'var(--accent-dim)',
  color: 'var(--accent)',
  border: '1px solid var(--border-primary)',
  borderRadius: 'var(--radius-sm)',
  padding: '4px 12px',
  fontSize: 12,
  cursor: 'pointer',
};

const emptyStyle: React.CSSProperties = {
  textAlign: 'center',
  padding: 48,
  color: 'var(--text-secondary)',
  fontSize: 14,
  background: 'var(--bg-surface)',
  borderRadius: 'var(--radius-sm)',
};

const aggHeaderStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  padding: '8px 12px',
  background: 'var(--bg-surface)',
  borderRadius: 'var(--radius-sm) var(--radius-sm) 0 0',
  borderBottom: '1px solid var(--border-primary)',
  color: 'var(--text-primary)',
};

const timelineItemStyle: React.CSSProperties = {
  display: 'flex',
  gap: 12,
  alignItems: 'flex-start',
  padding: '10px 12px',
  background: 'var(--bg-surface)',
  borderBottom: '1px solid var(--bg-primary)',
  cursor: 'pointer',
};

const dotStyle: React.CSSProperties = {
  width: 10,
  height: 10,
  borderRadius: '50%',
  marginTop: 3,
  flexShrink: 0,
};

const sectionHeadingStyle: React.CSSProperties = {
  fontSize: 13,
  fontWeight: 600,
  margin: '20px 0 10px',
  color: 'var(--text-secondary)',
};

const jsonPreStyle: React.CSSProperties = {
  background: 'var(--bg-primary)',
  borderRadius: 'var(--radius-sm)',
  padding: 12,
  fontSize: 11,
  fontFamily: 'var(--font-mono)',
  color: 'var(--text-primary)',
  overflow: 'auto',
  maxHeight: 300,
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-all',
};

const paginationBtnStyle = (disabled: boolean): React.CSSProperties => ({
  background: disabled ? 'var(--bg-surface)' : 'var(--accent-dim)',
  color: disabled ? 'var(--text-muted)' : 'var(--accent)',
  border: '1px solid var(--border-primary)',
  borderRadius: 'var(--radius-sm)',
  padding: '6px 16px',
  fontSize: 12,
  cursor: disabled ? 'default' : 'pointer',
  opacity: disabled ? 0.5 : 1,
});

export default EventTimeline;
