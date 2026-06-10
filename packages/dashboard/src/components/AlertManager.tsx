import { type FC, useEffect, useState, useCallback } from 'react';
import { useWatchlistStore } from '../stores/watchlistStore';
import { wsManager } from '../services/WebSocketManager';
import type { AlertTriggered } from '../types/websocket';

const AlertManager: FC = () => {
  const {
    alerts,
    notifications,
    loading,
    error,
    fetchAlerts,
    createAlert,
    deleteAlert,
    addNotification,
    clearNotifications,
  } = useWatchlistStore();

  const [instrument, setInstrument] = useState('');
  const [conditionType, setConditionType] = useState('price_above');
  const [conditionValue, setConditionValue] = useState('');

  const handleAlertEvent = useCallback(
    (data: AlertTriggered) => {
      addNotification(data);
    },
    [addNotification],
  );

  useEffect(() => {
    fetchAlerts();
  }, [fetchAlerts]);

  useEffect(() => {
    const subId = wsManager.subscribe('alerts', handleAlertEvent);
    return () => {
      wsManager.unsubscribe(subId);
    };
  }, [handleAlertEvent]);

  const handleCreate = async () => {
    const inst = instrument.trim();
    const val = parseFloat(conditionValue);
    if (!inst || isNaN(val)) return;
    await createAlert({
      instrument: inst,
      condition_type: conditionType,
      condition_value: { target: val },
    });
    setInstrument('');
    setConditionValue('');
  };

  return (
    <div style={{ padding: '12px', fontFamily: 'var(--font-sans)', color: 'var(--text-primary)' }}>
      <h3 style={{ margin: '0 0 12px', fontSize: '14px', color: 'var(--text-secondary)' }}>
        Alerts
      </h3>

      {error && (
        <p style={{ color: 'var(--danger)', fontSize: '12px', margin: '0 0 8px' }}>{error}</p>
      )}

      {/* Create alert form */}
      <div style={{ display: 'flex', gap: '6px', marginBottom: '12px', flexWrap: 'wrap' }}>
        <input
          type="text"
          placeholder="Instrument"
          value={instrument}
          onChange={(e) => setInstrument(e.target.value)}
          style={inputStyle}
          aria-label="Alert instrument"
        />
        <select
          value={conditionType}
          onChange={(e) => setConditionType(e.target.value)}
          style={inputStyle}
          aria-label="Condition type"
        >
          <option value="price_above">Price Above</option>
          <option value="price_below">Price Below</option>
          <option value="indicator">Indicator</option>
        </select>
        <input
          type="number"
          placeholder="Value"
          value={conditionValue}
          onChange={(e) => setConditionValue(e.target.value)}
          style={{ ...inputStyle, width: '80px' }}
          aria-label="Condition value"
        />
        <button
          onClick={handleCreate}
          disabled={loading || !instrument.trim() || !conditionValue}
          style={btnStyle}
        >
          Add Alert
        </button>
      </div>

      {/* Active alerts */}
      <h4 style={{ margin: '0 0 8px', fontSize: '13px', color: 'var(--text-secondary)' }}>
        Active Alerts {alerts.length > 0 && `(${alerts.length})`}
      </h4>

      {loading && <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Loading…</p>}

      {!loading && alerts.length === 0 && (
        <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>No active alerts</p>
      )}

      {alerts.map((alert) => (
        <div
          key={alert.id}
          style={{
            background: 'var(--bg-surface)',
            borderRadius: 'var(--radius-sm)',
            padding: '6px 10px',
            marginBottom: '6px',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <div>
            <span style={{ fontWeight: 600, fontSize: '12px' }}>{alert.instrument}</span>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)', marginLeft: '6px' }}>
              {alert.condition_type} {JSON.stringify(alert.condition_value)}
            </span>
          </div>
          <button
            onClick={() => deleteAlert(alert.id)}
            style={{ ...btnSmallStyle, color: 'var(--danger)' }}
            aria-label={`Delete alert for ${alert.instrument}`}
          >
            Delete
          </button>
        </div>
      ))}

      {/* Real-time notifications */}
      {notifications.length > 0 && (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '12px' }}>
            <h4 style={{ margin: 0, fontSize: '13px', color: 'var(--text-secondary)' }}>
              Triggered ({notifications.length})
            </h4>
            <button onClick={clearNotifications} style={btnSmallStyle}>
              Clear
            </button>
          </div>
          {notifications.map((n, i) => (
            <div
              key={`${n.id}-${i}`}
              style={{
                background: 'var(--bg-surface)',
                borderLeft: '3px solid var(--warning)',
                borderRadius: 'var(--radius-sm)',
                padding: '6px 10px',
                marginTop: '6px',
                fontSize: '12px',
              }}
            >
              <div style={{ fontWeight: 600 }}>{n.instrument}</div>
              <div style={{ color: 'var(--text-muted)', fontSize: '11px' }}>{n.message}</div>
            </div>
          ))}
        </>
      )}
    </div>
  );
};

const inputStyle: React.CSSProperties = {
  background: 'var(--bg-primary)',
  border: '1px solid var(--border-secondary)',
  borderRadius: 'var(--radius-sm)',
  color: 'var(--text-primary)',
  padding: '4px 8px',
  fontSize: '12px',
};

const btnStyle: React.CSSProperties = {
  background: 'var(--accent)',
  color: '#fff',
  border: 'none',
  borderRadius: 'var(--radius-sm)',
  padding: '4px 10px',
  fontSize: '12px',
  cursor: 'pointer',
};

const btnSmallStyle: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: 'var(--text-secondary)',
  fontSize: '11px',
  cursor: 'pointer',
  padding: '2px 4px',
};

export default AlertManager;
