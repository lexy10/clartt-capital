import { useCallback } from 'react';
import { useReplayStore } from '../stores/replayStore';
import type { Candle } from '../types/candle';

/** Generate sample historical candles for demo/testing purposes */
function generateSampleCandles(count: number): Candle[] {
  const candles: Candle[] = [];
  let price = 33000 + Math.random() * 1000;
  const now = Date.now();
  const intervalMs = 60_000; // 1-minute candles

  for (let i = 0; i < count; i++) {
    const open = price;
    const change = (Math.random() - 0.48) * 50;
    const close = open + change;
    const high = Math.max(open, close) + Math.random() * 30;
    const low = Math.min(open, close) - Math.random() * 30;
    const volume = Math.floor(100 + Math.random() * 900);

    candles.push({
      instrument: 'US30',
      timeframe: '1m',
      open: +open.toFixed(2),
      high: +high.toFixed(2),
      low: +low.toFixed(2),
      close: +close.toFixed(2),
      volume,
      timestamp: new Date(now - (count - i) * intervalMs).toISOString(),
    });
    price = close;
  }
  return candles;
}

export default function ReplayEngine() {
  const status = useReplayStore((s) => s.status);
  const speed = useReplayStore((s) => s.speed);
  const currentIndex = useReplayStore((s) => s.currentIndex);
  const totalCandles = useReplayStore((s) => s.historicalCandles.length);
  const play = useReplayStore((s) => s.play);
  const pause = useReplayStore((s) => s.pause);
  const resume = useReplayStore((s) => s.resume);
  const rewind = useReplayStore((s) => s.rewind);
  const step = useReplayStore((s) => s.step);
  const setSpeed = useReplayStore((s) => s.setSpeed);
  const loadCandles = useReplayStore((s) => s.loadCandles);

  const handleLoadSample = useCallback(() => {
    const candles = generateSampleCandles(200);
    loadCandles(candles);
  }, [loadCandles]);

  const handlePlayPause = useCallback(() => {
    if (status === 'playing') {
      pause();
    } else if (status === 'paused') {
      resume();
    } else {
      play();
    }
  }, [status, play, pause, resume]);

  const progress = totalCandles > 0 ? (currentIndex / totalCandles) * 100 : 0;

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={styles.title}>Replay Engine</span>
        <span style={styles.badge}>
          {status === 'idle' ? 'IDLE' : status === 'playing' ? 'PLAYING' : 'PAUSED'}
        </span>
      </div>

      {totalCandles === 0 ? (
        <button onClick={handleLoadSample} style={styles.loadBtn}>
          Load Sample Data (200 candles)
        </button>
      ) : (
        <>
          {/* Progress bar */}
          <div style={styles.progressContainer}>
            <div style={{ ...styles.progressBar, width: `${progress}%` }} />
          </div>
          <div style={styles.progressLabel}>
            {currentIndex} / {totalCandles} candles
          </div>

          {/* Controls */}
          <div style={styles.controls}>
            <button onClick={rewind} style={styles.btn} title="Rewind">
              ⏮
            </button>
            <button onClick={handlePlayPause} style={styles.btn} title={status === 'playing' ? 'Pause' : 'Play'}>
              {status === 'playing' ? '⏸' : '▶'}
            </button>
            <button onClick={step} style={styles.btn} title="Step Forward">
              ⏭
            </button>
          </div>

          {/* Speed slider */}
          <div style={styles.speedRow}>
            <label style={styles.speedLabel}>Speed: {speed}x</label>
            <input
              type="range"
              min={1}
              max={100}
              value={speed}
              onChange={(e) => setSpeed(Number(e.target.value))}
              style={styles.slider}
            />
          </div>

          <button onClick={handleLoadSample} style={styles.reloadBtn}>
            Reload Data
          </button>
        </>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    padding: '12px',
    background: 'var(--bg-secondary, #1e1e2e)',
    borderRadius: '8px',
    border: '1px solid var(--border-primary, #333)',
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  title: {
    fontSize: '13px',
    fontWeight: 600,
    color: 'var(--text-primary, #e0e0e0)',
  },
  badge: {
    fontSize: '10px',
    fontWeight: 700,
    padding: '2px 6px',
    borderRadius: '4px',
    background: 'var(--accent-primary, #4a9eff)',
    color: '#fff',
  },
  loadBtn: {
    padding: '8px',
    background: 'var(--accent-primary, #4a9eff)',
    color: '#fff',
    border: 'none',
    borderRadius: '4px',
    cursor: 'pointer',
    fontSize: '12px',
  },
  progressContainer: {
    height: '4px',
    background: 'var(--bg-tertiary, #2a2a3e)',
    borderRadius: '2px',
    overflow: 'hidden',
  },
  progressBar: {
    height: '100%',
    background: 'var(--accent-primary, #4a9eff)',
    transition: 'width 0.1s ease',
  },
  progressLabel: {
    fontSize: '11px',
    color: 'var(--text-secondary, #888)',
    textAlign: 'center' as const,
  },
  controls: {
    display: 'flex',
    justifyContent: 'center',
    gap: '8px',
  },
  btn: {
    width: '36px',
    height: '36px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'var(--bg-tertiary, #2a2a3e)',
    color: 'var(--text-primary, #e0e0e0)',
    border: '1px solid var(--border-primary, #333)',
    borderRadius: '6px',
    cursor: 'pointer',
    fontSize: '16px',
  },
  speedRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
  },
  speedLabel: {
    fontSize: '11px',
    color: 'var(--text-secondary, #888)',
    minWidth: '60px',
  },
  slider: {
    flex: 1,
    accentColor: 'var(--accent-primary, #4a9eff)',
  },
  reloadBtn: {
    padding: '4px 8px',
    background: 'transparent',
    color: 'var(--text-secondary, #888)',
    border: '1px solid var(--border-primary, #333)',
    borderRadius: '4px',
    cursor: 'pointer',
    fontSize: '11px',
  },
};
