import { type FC, useEffect, useCallback } from 'react';
import { useHealthStore, type CircuitBreakerInfo, type ServiceHealth, type ServiceDependency } from '../stores/healthStore';

/* ── Friendly display names ── */
const SERVICE_META: Record<string, { label: string; description: string; icon: string }> = {
  backend:            { label: 'API Gateway',        description: 'NestJS backend — REST API, WebSocket relay, database',  icon: '🖥️' },
  'strategy-engine':  { label: 'Strategy Engine',    description: 'Signal generation, backtesting, agent orchestration',   icon: '📊' },
  'execution-engine': { label: 'Execution Engine',   description: 'Trade execution, risk management, broker integration', icon: '⚡' },
};

const DEP_LABELS: Record<string, string> = {
  postgresql: 'PostgreSQL',
  redis: 'Redis',
  'execution-engine': 'Execution Engine',
  'backend-config': 'Config Loader',
  'backend-signals': 'Signal Publisher',
  metaapi: 'MetaAPI Broker',
};

const CB_LABELS: Record<string, string> = {
  'backend-to-execution-engine': 'API Gateway → Execution Engine',
  'strategy-engine-to-backend-config': 'Strategy Engine → Config API',
  'strategy-engine-to-backend-signals': 'Strategy Engine → Signals API',
  'execution-engine-to-metaapi': 'Execution Engine → MetaAPI Broker',
};

const STATUS_CONFIG: Record<string, { color: string; bg: string; label: string }> = {
  healthy:   { color: '#22c55e', bg: 'rgba(34,197,94,0.08)',  label: 'Healthy' },
  degraded:  { color: '#f59e0b', bg: 'rgba(245,158,11,0.08)', label: 'Degraded' },
  unhealthy: { color: '#ef4444', bg: 'rgba(239,68,68,0.08)',  label: 'Unhealthy' },
};

const CB_STATE_CONFIG: Record<string, { color: string; bg: string; label: string }> = {
  closed:    { color: '#22c55e', bg: 'rgba(34,197,94,0.1)',   label: 'Closed' },
  open:      { color: '#ef4444', bg: 'rgba(239,68,68,0.1)',   label: 'Open' },
  half_open: { color: '#f59e0b', bg: 'rgba(245,158,11,0.1)',  label: 'Half-Open' },
};

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 0) return 'just now';
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ${mins % 60}m ago`;
}

/* ── Status pill ── */
const StatusPill: FC<{ status: string }> = ({ status }) => {
  const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG.unhealthy;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      padding: '3px 10px', borderRadius: 12, fontSize: 12, fontWeight: 600,
      color: cfg.color, background: cfg.bg,
    }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: cfg.color, display: 'inline-block' }} />
      {cfg.label}
    </span>
  );
};

/* ── Dependency row ── */
const DependencyRow: FC<{ dep: ServiceDependency }> = ({ dep }) => {
  const isHealthy = dep.status === 'healthy';
  const cbState = dep.circuitBreakerState;
  const cbCfg = cbState ? CB_STATE_CONFIG[cbState] : null;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '8px 12px', borderRadius: 6,
      background: 'var(--bg-secondary, #1a1a2e)',
      fontSize: 13,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{
          width: 8, height: 8, borderRadius: '50%',
          background: isHealthy ? '#22c55e' : '#ef4444',
          flexShrink: 0,
        }} />
        <span style={{ color: 'var(--text-primary, #e2e8f0)' }}>
          {DEP_LABELS[dep.name] ?? dep.name}
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        {cbCfg && (
          <span style={{
            fontSize: 11, padding: '2px 7px', borderRadius: 8,
            color: cbCfg.color, background: cbCfg.bg,
          }}>
            CB: {cbCfg.label}
          </span>
        )}
        <span style={{ fontSize: 11, color: '#6b7280' }}>
          {dep.lastSuccessfulContact ? timeAgo(dep.lastSuccessfulContact) : '—'}
        </span>
      </div>
    </div>
  );
};

/* ── Service card ── */
const ServiceCard: FC<{ health: ServiceHealth }> = ({ health }) => {
  const meta = SERVICE_META[health.service] ?? { label: health.service, description: '', icon: '🔧' };
  const statusCfg = STATUS_CONFIG[health.status] ?? STATUS_CONFIG.unhealthy;

  return (
    <div style={{
      borderRadius: 12, padding: 20,
      border: `1px solid ${statusCfg.color}30`,
      background: 'var(--bg-card, #16162a)',
      display: 'flex', flexDirection: 'column', gap: 14,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 24 }}>{meta.icon}</span>
          <div>
            <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary, #e2e8f0)' }}>{meta.label}</div>
            <div style={{ fontSize: 12, color: '#6b7280', marginTop: 2 }}>{meta.description}</div>
          </div>
        </div>
        <StatusPill status={health.status} />
      </div>

      {health.dependencies.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', textTransform: 'uppercase', letterSpacing: 0.5 }}>
            Dependencies
          </div>
          {health.dependencies.map((dep) => (
            <DependencyRow key={dep.name} dep={dep} />
          ))}
        </div>
      )}

      <div style={{ fontSize: 11, color: '#4b5563', textAlign: 'right' }}>
        Last check: {health.timestamp ? timeAgo(health.timestamp) : '—'}
      </div>
    </div>
  );
};

/* ── Circuit breaker card ── */
const CircuitBreakerCard: FC<{ cb: CircuitBreakerInfo }> = ({ cb }) => {
  const cfg = CB_STATE_CONFIG[cb.state] ?? CB_STATE_CONFIG.open;
  return (
    <div style={{
      padding: '14px 16px', borderRadius: 10,
      border: `1px solid ${cfg.color}30`,
      background: cfg.bg,
      display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary, #e2e8f0)' }}>
          {CB_LABELS[cb.name] ?? cb.name}
        </span>
        <span style={{
          fontSize: 11, padding: '2px 8px', borderRadius: 8,
          color: cfg.color, background: `${cfg.color}20`, fontWeight: 600,
        }}>
          {cfg.label}
        </span>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#6b7280' }}>
        <span>Failures: {cb.failureCount}</span>
        <span>Changed: {timeAgo(cb.lastStateChange)}</span>
      </div>
    </div>
  );
};

/* ── Main panel ── */
const SystemHealthPanel: FC = () => {
  const { services, circuitBreakers, consumerLags, warningBannerVisible, loading, lastRefresh, fetchHealthSnapshot, subscribeToHealth } =
    useHealthStore();

  useEffect(() => {
    fetchHealthSnapshot();
    subscribeToHealth();
    const interval = setInterval(fetchHealthSnapshot, 30000);
    return () => clearInterval(interval);
  }, [fetchHealthSnapshot, subscribeToHealth]);

  const handleRefresh = useCallback(() => { fetchHealthSnapshot(); }, [fetchHealthSnapshot]);

  const allHealthy = services.length > 0 && services.every((s) => s.status === 'healthy');

  if (loading && services.length === 0) {
    return (
      <div style={{ padding: 24, display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 200 }}>
        <span style={{ color: '#6b7280', fontSize: 14 }}>Loading health data…</span>
      </div>
    );
  }

  return (
    <div style={{ padding: '20px 24px' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: 'var(--text-primary, #e2e8f0)' }}>
            System Health
          </h2>
          <p style={{ margin: '4px 0 0', fontSize: 13, color: '#6b7280' }}>
            Real-time status of all platform services and connections
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {lastRefresh && (
            <span style={{ fontSize: 11, color: '#4b5563' }}>Updated {timeAgo(lastRefresh)}</span>
          )}
          <button
            onClick={handleRefresh}
            style={{
              padding: '6px 14px', borderRadius: 8, border: '1px solid #374151',
              background: 'transparent', color: '#9ca3af', fontSize: 12, cursor: 'pointer',
            }}
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      {/* Warning banner */}
      {warningBannerVisible && (
        <div style={{
          padding: '10px 16px', borderRadius: 8, marginBottom: 16,
          background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)',
          color: '#fca5a5', fontSize: 13, display: 'flex', alignItems: 'center', gap: 8,
        }}>
          ⚠️ One or more circuit breakers are open — some connections may be degraded
        </div>
      )}

      {/* Overall status bar */}
      <div style={{
        padding: '12px 16px', borderRadius: 10, marginBottom: 20,
        background: allHealthy ? 'rgba(34,197,94,0.06)' : 'rgba(245,158,11,0.06)',
        border: `1px solid ${allHealthy ? 'rgba(34,197,94,0.2)' : 'rgba(245,158,11,0.2)'}`,
        display: 'flex', alignItems: 'center', gap: 10, fontSize: 14,
      }}>
        <span style={{
          width: 10, height: 10, borderRadius: '50%',
          background: allHealthy ? '#22c55e' : '#f59e0b',
        }} />
        <span style={{ fontWeight: 600, color: 'var(--text-primary, #e2e8f0)' }}>
          {allHealthy ? 'All systems operational' : 'Some systems need attention'}
        </span>
        <span style={{ color: '#6b7280', fontSize: 12, marginLeft: 'auto' }}>
          {services.filter((s) => s.status === 'healthy').length}/{services.length} services healthy
        </span>
      </div>

      {/* Service cards */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(3, 1fr)',
        gap: 16, marginBottom: 24,
      }}>
        {services.map((svc) => (
          <ServiceCard key={svc.service} health={svc} />
        ))}
      </div>

      {/* Circuit breakers section */}
      {circuitBreakers.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <h3 style={{ margin: '0 0 12px', fontSize: 16, fontWeight: 600, color: 'var(--text-primary, #e2e8f0)' }}>
            Circuit Breakers
          </h3>
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
            gap: 10,
          }}>
            {circuitBreakers.map((cb) => (
              <CircuitBreakerCard key={cb.name} cb={cb} />
            ))}
          </div>
        </div>
      )}

      {/* Consumer lag section */}
      {consumerLags.length > 0 && (
        <div>
          <h3 style={{ margin: '0 0 12px', fontSize: 16, fontWeight: 600, color: 'var(--text-primary, #e2e8f0)' }}>
            Stream Consumer Lag
          </h3>
          <div style={{
            borderRadius: 10, overflow: 'hidden',
            border: '1px solid #1f2937',
          }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ background: 'var(--bg-secondary, #1a1a2e)' }}>
                  <th style={{ padding: '10px 14px', textAlign: 'left', color: '#6b7280', fontWeight: 600, fontSize: 11, textTransform: 'uppercase' }}>Stream</th>
                  <th style={{ padding: '10px 14px', textAlign: 'left', color: '#6b7280', fontWeight: 600, fontSize: 11, textTransform: 'uppercase' }}>Consumer Group</th>
                  <th style={{ padding: '10px 14px', textAlign: 'right', color: '#6b7280', fontWeight: 600, fontSize: 11, textTransform: 'uppercase' }}>Lag</th>
                  <th style={{ padding: '10px 14px', textAlign: 'right', color: '#6b7280', fontWeight: 600, fontSize: 11, textTransform: 'uppercase' }}>Threshold</th>
                </tr>
              </thead>
              <tbody>
                {consumerLags.map((l) => (
                  <tr key={`${l.stream}-${l.group}`} style={{ borderTop: '1px solid #1f2937' }}>
                    <td style={{ padding: '10px 14px', color: 'var(--text-primary, #e2e8f0)' }}>{l.stream}</td>
                    <td style={{ padding: '10px 14px', color: '#9ca3af' }}>{l.group}</td>
                    <td style={{
                      padding: '10px 14px', textAlign: 'right', fontWeight: 600,
                      color: l.lag > l.threshold ? '#ef4444' : '#22c55e',
                    }}>{l.lag}</td>
                    <td style={{ padding: '10px 14px', textAlign: 'right', color: '#6b7280' }}>{l.threshold}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};

export default SystemHealthPanel;
