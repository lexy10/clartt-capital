import { type FC, useEffect, useState } from 'react';
import { useReconciliationStore, type ReconciliationConfig } from '../stores/reconciliationStore';

const ReconciliationConfigPanel: FC = () => {
  const { config, fetchConfig, updateConfig } = useReconciliationStore();
  const [form, setForm] = useState<Partial<ReconciliationConfig>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  useEffect(() => {
    if (config) setForm(config);
  }, [config]);

  const handleSave = async () => {
    setSaving(true);
    await updateConfig(form);
    setSaving(false);
  };

  if (!config) return <p>Loading config…</p>;

  const inputStyle = { padding: '6px 8px', borderRadius: '4px', border: '1px solid #374151', width: '120px' };

  return (
    <div>
      <h3 style={{ margin: '0 0 12px' }}>Reconciliation Config</h3>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', maxWidth: '500px' }}>
        <label>
          Interval (seconds)
          <input
            type="number"
            min={30}
            value={form.reconciliationIntervalSeconds ?? ''}
            onChange={(e) => setForm({ ...form, reconciliationIntervalSeconds: Number(e.target.value) })}
            style={inputStyle}
          />
        </label>
        <label>
          Balance drift threshold
          <input
            type="number"
            step="0.01"
            min={0.01}
            value={form.balanceDriftThreshold ?? ''}
            onChange={(e) => setForm({ ...form, balanceDriftThreshold: Number(e.target.value) })}
            style={inputStyle}
          />
        </label>
        <label>
          Equity drift threshold
          <input
            type="number"
            step="0.01"
            min={0.01}
            value={form.equityDriftThreshold ?? ''}
            onChange={(e) => setForm({ ...form, equityDriftThreshold: Number(e.target.value) })}
            style={inputStyle}
          />
        </label>
        <label>
          Position size threshold
          <input
            type="number"
            step="0.001"
            min={0.001}
            value={form.positionSizeDriftThreshold ?? ''}
            onChange={(e) => setForm({ ...form, positionSizeDriftThreshold: Number(e.target.value) })}
            style={inputStyle}
          />
        </label>
        <label>
          Escalation cycles
          <input
            type="number"
            min={1}
            value={form.escalationCycleCount ?? ''}
            onChange={(e) => setForm({ ...form, escalationCycleCount: Number(e.target.value) })}
            style={inputStyle}
          />
        </label>
      </div>

      <div style={{ marginTop: '16px', display: 'flex', flexDirection: 'column', gap: '8px', maxWidth: '300px' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <input
            type="checkbox"
            checked={form.autoCorrectPhantomPositions ?? false}
            onChange={(e) => setForm({ ...form, autoCorrectPhantomPositions: e.target.checked })}
          />
          Auto-correct phantom positions
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <input
            type="checkbox"
            checked={form.autoCorrectMissingPositions ?? false}
            onChange={(e) => setForm({ ...form, autoCorrectMissingPositions: e.target.checked })}
          />
          Auto-correct missing positions
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <input
            type="checkbox"
            checked={form.autoCorrectBalanceDrift ?? false}
            onChange={(e) => setForm({ ...form, autoCorrectBalanceDrift: e.target.checked })}
          />
          Auto-correct balance drift
        </label>
      </div>

      <button
        onClick={handleSave}
        disabled={saving}
        style={{ marginTop: '16px', padding: '8px 16px', borderRadius: '4px', cursor: 'pointer' }}
      >
        {saving ? 'Saving…' : 'Save Config'}
      </button>
    </div>
  );
};

export default ReconciliationConfigPanel;
