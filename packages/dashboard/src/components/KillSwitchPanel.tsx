import { type FC, useState, useEffect, useCallback } from 'react';
import { apiClient } from '../services/ApiClient';
import { wsManager } from '../services/WebSocketManager';
import type { SystemStatus } from '../types/api';
import type { KillSwitchStatus } from '../types/websocket';

const KillSwitchPanel: FC = () => {
  const [killSwitchActive, setKillSwitchActive] = useState(false);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showConfirm, setShowConfirm] = useState(false);
  const [selectedMode, setSelectedMode] = useState<'soft' | 'hard'>('soft');

  useEffect(() => {
    apiClient.admin.getSystemStatus().then((raw: any) => {
      // Backend returns { killSwitch: { isActive }, system: { uptime } }
      // Normalize to the shape we need
      const normalized: SystemStatus = {
        kill_switch_active: raw.kill_switch_active ?? raw.killSwitch?.isActive ?? false,
        services: raw.services ?? {},
        uptime_seconds: raw.uptime_seconds ?? raw.system?.uptime ?? 0,
      };
      setSystemStatus(normalized);
      setKillSwitchActive(normalized.kill_switch_active);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    const subId = wsManager.subscribe('kill_switch', (data: KillSwitchStatus) => {
      setKillSwitchActive(data.is_active);
    });
    return () => { wsManager.unsubscribe(subId); };
  }, []);

  const handleToggle = useCallback(async (mode?: 'soft' | 'hard') => {
    setShowConfirm(false);
    setLoading(true);
    setError(null);
    try {
      if (killSwitchActive) {
        await apiClient.admin.deactivateKillSwitch();
        setKillSwitchActive(false);
      } else {
        await apiClient.admin.activateKillSwitch(mode ?? 'soft');
        setKillSwitchActive(true);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Kill switch operation failed');
    } finally {
      setLoading(false);
    }
  }, [killSwitchActive]);

  const statusColor = (s: string) => s === 'healthy' ? 'var(--success)' : s === 'degraded' ? 'var(--warning)' : 'var(--danger)';

  return (
    <div>
      {error && <div className="badge badge-danger" style={{ marginBottom: 8, display: 'block', textAlign: 'center' }}>{error}</div>}

      <div className="card" style={{ marginBottom: 12 }}>
        <div className="card-header">
          <span className="card-title">Kill Switch</span>
          <span className={`badge ${killSwitchActive ? 'badge-danger' : 'badge-success'}`}>
            {killSwitchActive ? 'Active' : 'Off'}
          </span>
        </div>

        <div style={{
          padding: '10px 12px',
          background: killSwitchActive ? 'var(--danger-bg)' : 'var(--success-bg)',
          borderRadius: 'var(--radius-md)',
          fontSize: 12,
          fontWeight: 500,
          color: killSwitchActive ? 'var(--danger)' : 'var(--success)',
          marginBottom: 12,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <span style={{
            width: 8, height: 8, borderRadius: '50%',
            background: killSwitchActive ? 'var(--danger)' : 'var(--success)',
            boxShadow: `0 0 6px ${killSwitchActive ? 'var(--danger)' : 'var(--success)'}`,
          }} />
          {killSwitchActive ? 'All trading halted' : 'Trading active'}
        </div>

        <button
          onClick={() => setShowConfirm(true)}
          disabled={loading}
          style={{
            width: '100%',
            padding: '10px',
            borderRadius: 'var(--radius-md)',
            border: `1px solid ${killSwitchActive ? 'var(--success-border)' : 'var(--danger-border)'}`,
            background: killSwitchActive ? 'var(--success-bg)' : 'var(--danger-bg)',
            color: killSwitchActive ? 'var(--success)' : 'var(--danger)',
            fontSize: 12,
            fontWeight: 600,
            cursor: loading ? 'not-allowed' : 'pointer',
            transition: 'all var(--transition-fast)',
            opacity: loading ? 0.5 : 1,
          }}
        >
          {loading ? 'Processing…' : killSwitchActive ? 'Deactivate Kill Switch' : 'Activate Kill Switch'}
        </button>
      </div>

      {/* Service status */}
      {systemStatus && systemStatus.services && Object.keys(systemStatus.services).length > 0 && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Services</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {Object.entries(systemStatus.services).map(([name, status]) => (
              <div key={name} style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                padding: '6px 8px', background: 'var(--bg-surface)', borderRadius: 'var(--radius-sm)', fontSize: 11,
              }}>
                <span style={{ color: 'var(--text-secondary)', textTransform: 'capitalize' }}>{name.replace('_', ' ')}</span>
                <span style={{ color: statusColor(status), fontWeight: 600, fontFamily: 'var(--font-mono)' }}>{status}</span>
              </div>
            ))}
          </div>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 8 }}>
            Uptime: {Math.floor(systemStatus.uptime_seconds / 3600)}h {Math.floor((systemStatus.uptime_seconds % 3600) / 60)}m
          </div>
        </div>
      )}

      {/* Confirm dialog */}
      {showConfirm && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
          backdropFilter: 'blur(4px)',
        }} role="dialog" aria-modal="true">
          <div style={{
            background: 'var(--bg-secondary)', border: '1px solid var(--border-primary)',
            borderRadius: 'var(--radius-xl)', padding: 24, maxWidth: 400, width: '90%', textAlign: 'center',
          }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>
              {killSwitchActive ? 'Resume Trading?' : 'Halt All Trading?'}
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '0 0 20px', lineHeight: 1.6 }}>
              {killSwitchActive
                ? 'This will resume signal processing and trade execution.'
                : 'Choose how to halt trading:'}
            </p>

            {!killSwitchActive && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>
                <button
                  onClick={() => setSelectedMode('soft')}
                  style={{
                    padding: '10px 12px', borderRadius: 'var(--radius-md)', textAlign: 'left',
                    border: `2px solid ${selectedMode === 'soft' ? 'var(--warning)' : 'var(--border-primary)'}`,
                    background: selectedMode === 'soft' ? 'var(--warning-bg, rgba(255,170,0,0.1))' : 'var(--bg-surface)',
                    color: 'var(--text-primary)', cursor: 'pointer',
                  }}
                >
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 2 }}>⚠️ Soft — Stop Trading</div>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Stops new trades. Open positions stay active.</div>
                </button>
                <button
                  onClick={() => setSelectedMode('hard')}
                  style={{
                    padding: '10px 12px', borderRadius: 'var(--radius-md)', textAlign: 'left',
                    border: `2px solid ${selectedMode === 'hard' ? 'var(--danger)' : 'var(--border-primary)'}`,
                    background: selectedMode === 'hard' ? 'var(--danger-bg)' : 'var(--bg-surface)',
                    color: 'var(--text-primary)', cursor: 'pointer',
                  }}
                >
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 2 }}>🛑 Hard — Stop + Close All</div>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Stops new trades and closes all open positions immediately.</div>
                </button>
              </div>
            )}

            <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
              <button onClick={() => setShowConfirm(false)} style={{
                padding: '8px 20px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-primary)',
                background: 'var(--bg-surface)', color: 'var(--text-secondary)', fontSize: 12, fontWeight: 500, cursor: 'pointer',
              }}>Cancel</button>
              <button onClick={() => handleToggle(killSwitchActive ? undefined : selectedMode)} style={{
                padding: '8px 20px', borderRadius: 'var(--radius-md)', border: 'none',
                background: killSwitchActive ? 'var(--success)' : selectedMode === 'hard' ? 'var(--danger)' : 'var(--warning, #ffa500)',
                color: '#fff', fontSize: 12, fontWeight: 600, cursor: 'pointer',
              }}>{killSwitchActive ? 'Deactivate' : selectedMode === 'hard' ? 'Activate Hard Kill' : 'Activate Soft Kill'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default KillSwitchPanel;
