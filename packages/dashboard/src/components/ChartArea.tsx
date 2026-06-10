import { type FC, useEffect, useState } from 'react';
import ChartView from './ChartView';
import TimeframeSelector from './TimeframeSelector';
import { useChartStore } from '../stores/chartStore';
import { apiClient } from '../services/ApiClient';
import type { Instrument } from '../types/api';
import type { Signal } from '../types/signal';
import { wsManager } from '../services/WebSocketManager';

interface ChartAreaProps {
  isFullscreen: boolean;
  onToggleFullscreen: () => void;
  showFullscreen?: boolean;
}

const ChartArea: FC<ChartAreaProps> = ({ isFullscreen, onToggleFullscreen, showFullscreen = true }) => {
  const instrument = useChartStore((s) => s.instrument);
  const timeframe = useChartStore((s) => s.timeframe);
  const loading = useChartStore((s) => s.loading);
  const error = useChartStore((s) => s.error);
  const setInstrument = useChartStore((s) => s.setInstrument);
  const setTimeframe = useChartStore((s) => s.setTimeframe);
  const fetchCandles = useChartStore((s) => s.fetchCandles);

  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [instrumentsLoading, setInstrumentsLoading] = useState(true);
  const [signals, setSignals] = useState<Signal[]>([]);

  useEffect(() => {
    let cancelled = false;
    apiClient.instruments.list().then((list) => {
      if (cancelled) return;
      const active = list.filter((i) => i.isActive);
      setInstruments(active);
      if (active.length > 0 && !instrument) {
        setInstrument(active[0].symbol);
      }
      setInstrumentsLoading(false);
    }).catch(() => {
      if (!cancelled) setInstrumentsLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (instrument) fetchCandles();
  }, [instrument, timeframe, fetchCandles]);

  useEffect(() => {
    if (!instrument) {
      setSignals([]);
      return;
    }

    let cancelled = false;
    apiClient.signals.getRecent({ instrument, limit: 30 })
      .then((data) => {
        if (!cancelled) setSignals(data);
      })
      .catch(() => {
        if (!cancelled) setSignals([]);
      });

    const subId = wsManager.subscribe('signals', (signal: Signal) => {
      if (signal.instrument === instrument) {
        setSignals((prev) => [signal, ...prev.filter((item) => item.id !== signal.id)].slice(0, 30));
      }
    });

    return () => {
      cancelled = true;
      wsManager.unsubscribe(subId);
    };
  }, [instrument]);

  return (
    <>
      <div className="chart-toolbar">
        <div className="chart-toolbar-left">
          {instrumentsLoading ? (
            <span style={{ color: '#8b949e', fontSize: 13 }}>Loading instruments…</span>
          ) : instruments.length === 0 ? (
            <span style={{ color: '#8b949e', fontSize: 13 }}>No instruments available</span>
          ) : (
            <div style={{ display: 'flex', gap: 2, marginRight: 8 }}>
              {instruments.map(({ symbol, displayName }) => (
                <button
                  key={symbol}
                  className={`tf-btn mono${symbol === instrument ? ' tf-btn-active' : ''}`}
                  aria-label={`${displayName || symbol} instrument`}
                  aria-pressed={symbol === instrument}
                  onClick={() => setInstrument(symbol)}
                >
                  {displayName || symbol}
                </button>
              ))}
            </div>
          )}
          <div style={{ width: 1, height: 18, background: '#30363d', margin: '0 6px' }} />
          <TimeframeSelector active={timeframe} onChange={setTimeframe} />
        </div>
        <div className="chart-toolbar-right">
          {showFullscreen && (
            <button
              className="chart-fullscreen-btn"
              onClick={onToggleFullscreen}
              title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen chart'}
              aria-label={isFullscreen ? 'Exit fullscreen' : 'Fullscreen chart'}
            >
              {isFullscreen ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="4 14 10 14 10 20" />
                  <polyline points="20 10 14 10 14 4" />
                  <line x1="14" y1="10" x2="21" y2="3" />
                  <line x1="3" y1="21" x2="10" y2="14" />
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="15 3 21 3 21 9" />
                  <polyline points="9 21 3 21 3 15" />
                  <line x1="21" y1="3" x2="14" y2="10" />
                  <line x1="3" y1="21" x2="10" y2="14" />
                </svg>
              )}
            </button>
          )}
        </div>
      </div>
      <div className="chart-container">
        {instrument ? (
          <>
            <ChartView instrument={instrument} timeframe={timeframe} signals={signals} />
            {loading && (
              <div style={{
                position: 'absolute', inset: 0,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: 'rgba(13, 17, 23, 0.7)',
                backdropFilter: 'blur(2px)',
                zIndex: 10,
                transition: 'opacity 0.2s',
              }}>
                <div style={{
                  width: 24, height: 24,
                  border: '2px solid #30363d',
                  borderTopColor: '#818cf8',
                  borderRadius: '50%',
                  animation: 'spin 0.8s linear infinite',
                }} />
              </div>
            )}
            {error && (
              <div style={{
                position: 'absolute', bottom: 12, left: '50%', transform: 'translateX(-50%)',
                background: 'rgba(248, 81, 73, 0.15)',
                border: '1px solid rgba(248, 81, 73, 0.3)',
                borderRadius: 8, padding: '6px 14px',
                color: '#f85149', fontSize: 12, zIndex: 10,
              }}>
                {error}
              </div>
            )}
          </>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#8b949e' }}>
            {instrumentsLoading ? 'Loading…' : 'Select an instrument to view chart'}
          </div>
        )}
      </div>
    </>
  );
};

export default ChartArea;
