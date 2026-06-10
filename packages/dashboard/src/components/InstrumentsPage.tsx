import { type FC, useEffect, useState } from 'react';
import { useInstrumentStore } from '../stores/instrumentStore';
import type { Instrument, CreateInstrumentDto, UpdateInstrumentDto } from '../types/api';

const INSTRUMENT_TYPES: Array<'index' | 'commodity' | 'synthetic'> = ['index', 'commodity', 'synthetic'];

const InstrumentsPage: FC = () => {
  const { instruments, loading, error, fetchInstruments, createInstrument, updateInstrument, deleteInstrument } = useInstrumentStore();
  const [showAddForm, setShowAddForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);

  // Add form state
  const [addSymbol, setAddSymbol] = useState('');
  const [addDisplayName, setAddDisplayName] = useState('');
  const [addType, setAddType] = useState<'index' | 'commodity' | 'synthetic'>('index');
  const [addContractSize, setAddContractSize] = useState('1');
  const [addPipSize, setAddPipSize] = useState('0.01');
  const [addPipValue, setAddPipValue] = useState('1');
  const [addMinLot, setAddMinLot] = useState('0.01');
  const [addLotStep, setAddLotStep] = useState('0.01');
  const [addLeverage, setAddLeverage] = useState('100');

  // Edit form state
  const [editDisplayName, setEditDisplayName] = useState('');
  const [editType, setEditType] = useState<'index' | 'commodity' | 'synthetic'>('index');
  const [editActive, setEditActive] = useState(true);
  const [editContractSize, setEditContractSize] = useState('1');
  const [editPipSize, setEditPipSize] = useState('0.01');
  const [editPipValue, setEditPipValue] = useState('1');
  const [editMinLot, setEditMinLot] = useState('0.01');
  const [editLotStep, setEditLotStep] = useState('0.01');
  const [editLeverage, setEditLeverage] = useState('100');

  useEffect(() => { fetchInstruments(true); }, [fetchInstruments]);
  useEffect(() => { if (error) setDismissed(false); }, [error]);

  const resetAddForm = () => {
    setAddSymbol(''); setAddDisplayName(''); setAddType('index');
    setAddContractSize('1'); setAddPipSize('0.01'); setAddPipValue('1');
    setAddMinLot('0.01'); setAddLotStep('0.01'); setAddLeverage('100');
    setShowAddForm(false);
  };

  const handleCreate = async () => {
    const dto: CreateInstrumentDto = {
      symbol: addSymbol.trim(),
      displayName: addDisplayName.trim(),
      type: addType,
      contractSize: parseFloat(addContractSize) || 1,
      pipSize: parseFloat(addPipSize) || 0.01,
      pipValue: parseFloat(addPipValue) || 1,
      minLot: parseFloat(addMinLot) || 0.01,
      lotStep: parseFloat(addLotStep) || 0.01,
      leverage: parseInt(addLeverage) || 100,
    };
    await createInstrument(dto);
    if (!useInstrumentStore.getState().error) resetAddForm();
  };

  const startEdit = (inst: Instrument) => {
    setEditingId(inst.id);
    setEditDisplayName(inst.displayName);
    setEditType(inst.type);
    setEditActive(inst.isActive);
    setEditContractSize(String(inst.contractSize ?? 1));
    setEditPipSize(String(inst.pipSize ?? 0.01));
    setEditPipValue(String(inst.pipValue ?? 1));
    setEditMinLot(String(inst.minLot ?? 0.01));
    setEditLotStep(String(inst.lotStep ?? 0.01));
    setEditLeverage(String(inst.leverage ?? 100));
  };

  const handleUpdate = async () => {
    if (!editingId) return;
    const dto: UpdateInstrumentDto = {
      displayName: editDisplayName.trim(),
      type: editType,
      isActive: editActive,
      contractSize: parseFloat(editContractSize) || 1,
      pipSize: parseFloat(editPipSize) || 0.01,
      pipValue: parseFloat(editPipValue) || 1,
      minLot: parseFloat(editMinLot) || 0.01,
      lotStep: parseFloat(editLotStep) || 0.01,
      leverage: parseInt(editLeverage) || 100,
    };
    await updateInstrument(editingId, dto);
    if (!useInstrumentStore.getState().error) setEditingId(null);
  };

  const handleDelete = async (id: string) => {
    await deleteInstrument(id);
    setDeletingId(null);
  };

  return (
    <div style={{ padding: '16px', fontFamily: 'var(--font-sans)', color: 'var(--text-primary)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
        <h2 style={{ margin: 0, fontSize: '16px' }}>Instruments</h2>
        <button onClick={() => setShowAddForm(true)} style={addBtnStyle}>+ Add Instrument</button>
      </div>

      {error && !dismissed && (
        <div style={errorBannerStyle} role="alert">
          <span>{error}</span>
          <button onClick={() => setDismissed(true)} style={dismissBtnStyle} aria-label="Dismiss error">✕</button>
        </div>
      )}

      {showAddForm && (
        <div style={formCardStyle}>
          <h3 style={{ margin: '0 0 12px', fontSize: '13px', fontWeight: 600 }}>New Instrument</h3>
          <div style={formGridStyle}>
            <label style={labelStyle}>
              Symbol
              <input style={inputStyle} value={addSymbol} onChange={e => setAddSymbol(e.target.value)} placeholder="e.g. US30" />
            </label>
            <label style={labelStyle}>
              Display Name
              <input style={inputStyle} value={addDisplayName} onChange={e => setAddDisplayName(e.target.value)} placeholder="e.g. Dow Jones" />
            </label>
            <label style={labelStyle}>
              Type
              <select style={inputStyle} value={addType} onChange={e => setAddType(e.target.value as typeof addType)}>
                {INSTRUMENT_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
          </div>
          <h4 style={specHeadingStyle}>Contract Specs</h4>
          <div style={formGridStyle}>
            <label style={labelStyle}>Contract Size<input style={inputStyle} type="number" step="any" value={addContractSize} onChange={e => setAddContractSize(e.target.value)} /></label>
            <label style={labelStyle}>Pip Size<input style={inputStyle} type="number" step="any" value={addPipSize} onChange={e => setAddPipSize(e.target.value)} /></label>
            <label style={labelStyle}>Pip Value ($/pip/lot)<input style={inputStyle} type="number" step="any" value={addPipValue} onChange={e => setAddPipValue(e.target.value)} /></label>
            <label style={labelStyle}>Min Lot<input style={inputStyle} type="number" step="any" value={addMinLot} onChange={e => setAddMinLot(e.target.value)} /></label>
            <label style={labelStyle}>Lot Step<input style={inputStyle} type="number" step="any" value={addLotStep} onChange={e => setAddLotStep(e.target.value)} /></label>
            <label style={labelStyle}>Leverage<input style={inputStyle} type="number" step="1" value={addLeverage} onChange={e => setAddLeverage(e.target.value)} /></label>
          </div>
          <div style={{ display: 'flex', gap: '8px', marginTop: '12px' }}>
            <button onClick={handleCreate} disabled={!addSymbol.trim() || !addDisplayName.trim()} style={addBtnStyle}>Create</button>
            <button onClick={resetAddForm} style={cancelBtnStyle}>Cancel</button>
          </div>
        </div>
      )}

      {loading && <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Loading instruments…</p>}

      {!loading && instruments.length === 0 && (
        <p style={{ fontSize: '13px', color: 'var(--text-muted)', textAlign: 'center', marginTop: '40px' }}>
          No instruments configured. Add your first instrument.
        </p>
      )}

      {instruments.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>Symbol</th>
                <th style={thStyle}>Display Name</th>
                <th style={thStyle}>Type</th>
                <th style={thStyle}>Active</th>
                <th style={thStyle}>Contract</th>
                <th style={thStyle}>Pip Size</th>
                <th style={thStyle}>Pip Value</th>
                <th style={thStyle}>Min Lot</th>
                <th style={thStyle}>Lot Step</th>
                <th style={thStyle}>Leverage</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {instruments.map(inst => (
                <tr key={inst.id} style={trStyle}>
                  {editingId === inst.id ? (
                    <>
                      <td style={tdStyle}><span style={{ fontWeight: 600 }}>{inst.symbol}</span></td>
                      <td style={tdStyle}>
                        <input style={inlineInputStyle} value={editDisplayName} onChange={e => setEditDisplayName(e.target.value)} />
                      </td>
                      <td style={tdStyle}>
                        <select style={inlineInputStyle} value={editType} onChange={e => setEditType(e.target.value as typeof editType)}>
                          {INSTRUMENT_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                        </select>
                      </td>
                      <td style={tdStyle}>
                        <input type="checkbox" checked={editActive} onChange={e => setEditActive(e.target.checked)} />
                      </td>
                      <td style={tdStyle}><input style={inlineInputStyle} type="number" step="any" value={editContractSize} onChange={e => setEditContractSize(e.target.value)} /></td>
                      <td style={tdStyle}><input style={inlineInputStyle} type="number" step="any" value={editPipSize} onChange={e => setEditPipSize(e.target.value)} /></td>
                      <td style={tdStyle}><input style={inlineInputStyle} type="number" step="any" value={editPipValue} onChange={e => setEditPipValue(e.target.value)} /></td>
                      <td style={tdStyle}><input style={inlineInputStyle} type="number" step="any" value={editMinLot} onChange={e => setEditMinLot(e.target.value)} /></td>
                      <td style={tdStyle}><input style={inlineInputStyle} type="number" step="any" value={editLotStep} onChange={e => setEditLotStep(e.target.value)} /></td>
                      <td style={tdStyle}><input style={inlineInputStyle} type="number" step="1" value={editLeverage} onChange={e => setEditLeverage(e.target.value)} /></td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>
                        <button onClick={handleUpdate} style={saveBtnStyle}>Save</button>
                        <button onClick={() => setEditingId(null)} style={cancelBtnStyle}>Cancel</button>
                      </td>
                    </>
                  ) : (
                    <>
                      <td style={tdStyle}><span style={{ fontWeight: 600 }}>{inst.symbol}</span></td>
                      <td style={tdStyle}>{inst.displayName}</td>
                      <td style={tdStyle}><span style={badgeStyle}>{inst.type}</span></td>
                      <td style={tdStyle}><span style={{ color: inst.isActive ? 'var(--success, #22c55e)' : 'var(--text-muted)' }}>{inst.isActive ? '● Active' : '○ Inactive'}</span></td>
                      <td style={tdMono}>{inst.contractSize}</td>
                      <td style={tdMono}>{inst.pipSize}</td>
                      <td style={tdMono}>{inst.pipValue}</td>
                      <td style={tdMono}>{inst.minLot}</td>
                      <td style={tdMono}>{inst.lotStep}</td>
                      <td style={tdMono}>{inst.leverage}:1</td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>
                        <button onClick={() => startEdit(inst)} style={editBtnStyle}>Edit</button>
                        {deletingId === inst.id ? (
                          <span style={{ fontSize: '11px' }}>
                            <span style={{ color: 'var(--danger, #ef4444)', marginRight: '6px' }}>Delete?</span>
                            <button onClick={() => handleDelete(inst.id)} style={confirmDeleteBtnStyle}>Yes</button>
                            <button onClick={() => setDeletingId(null)} style={cancelBtnStyle}>No</button>
                          </span>
                        ) : (
                          <button onClick={() => setDeletingId(inst.id)} style={deleteBtnStyle}>Delete</button>
                        )}
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

// --- Styles ---

const addBtnStyle: React.CSSProperties = {
  background: 'var(--accent)',
  color: '#fff',
  border: 'none',
  borderRadius: 'var(--radius-sm)',
  padding: '6px 14px',
  fontSize: '12px',
  fontWeight: 600,
  cursor: 'pointer',
};

const errorBannerStyle: React.CSSProperties = {
  background: 'var(--danger-bg, rgba(239,68,68,0.1))',
  border: '1px solid var(--danger, #ef4444)',
  borderRadius: 'var(--radius-md)',
  padding: '8px 12px',
  marginBottom: '12px',
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  fontSize: '12px',
  color: 'var(--danger, #ef4444)',
};

const dismissBtnStyle: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: 'var(--danger, #ef4444)',
  fontSize: '14px',
  cursor: 'pointer',
  padding: '0 4px',
  lineHeight: 1,
};

const formCardStyle: React.CSSProperties = {
  background: 'var(--bg-secondary)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-md)',
  padding: '16px',
  marginBottom: '16px',
};

const formGridStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
  gap: '12px',
};

const labelStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '4px',
  fontSize: '11px',
  color: 'var(--text-muted)',
  fontWeight: 500,
};

const inputStyle: React.CSSProperties = {
  background: 'var(--bg-primary)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-sm)',
  padding: '6px 8px',
  fontSize: '12px',
  color: 'var(--text-primary)',
  outline: 'none',
};

const cancelBtnStyle: React.CSSProperties = {
  background: 'none',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-sm)',
  padding: '4px 10px',
  fontSize: '11px',
  color: 'var(--text-muted)',
  cursor: 'pointer',
};

const tableStyle: React.CSSProperties = {
  width: '100%',
  borderCollapse: 'collapse',
  fontSize: '12px',
};

const thStyle: React.CSSProperties = {
  textAlign: 'left',
  padding: '8px 10px',
  borderBottom: '1px solid var(--border)',
  color: 'var(--text-muted)',
  fontWeight: 500,
  fontSize: '11px',
  textTransform: 'uppercase',
  letterSpacing: '0.5px',
};

const trStyle: React.CSSProperties = {
  borderBottom: '1px solid var(--border)',
};

const tdStyle: React.CSSProperties = {
  padding: '8px 10px',
  verticalAlign: 'middle',
};

const inlineInputStyle: React.CSSProperties = {
  background: 'var(--bg-primary)',
  border: '1px solid var(--accent)',
  borderRadius: 'var(--radius-sm)',
  padding: '4px 6px',
  fontSize: '12px',
  color: 'var(--text-primary)',
  outline: 'none',
  width: '100%',
  maxWidth: '160px',
};

const badgeStyle: React.CSSProperties = {
  background: 'var(--bg-tertiary, rgba(255,255,255,0.05))',
  borderRadius: 'var(--radius-sm)',
  padding: '2px 8px',
  fontSize: '11px',
  textTransform: 'capitalize',
};

const editBtnStyle: React.CSSProperties = {
  background: 'none',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-sm)',
  padding: '3px 8px',
  fontSize: '11px',
  color: 'var(--text-primary)',
  cursor: 'pointer',
  marginRight: '4px',
};

const saveBtnStyle: React.CSSProperties = {
  background: 'var(--accent)',
  color: '#fff',
  border: 'none',
  borderRadius: 'var(--radius-sm)',
  padding: '3px 10px',
  fontSize: '11px',
  fontWeight: 600,
  cursor: 'pointer',
  marginRight: '4px',
};

const deleteBtnStyle: React.CSSProperties = {
  background: 'none',
  border: '1px solid var(--danger, #ef4444)',
  borderRadius: 'var(--radius-sm)',
  padding: '3px 8px',
  fontSize: '11px',
  color: 'var(--danger, #ef4444)',
  cursor: 'pointer',
};

const confirmDeleteBtnStyle: React.CSSProperties = {
  background: 'var(--danger, #ef4444)',
  color: '#fff',
  border: 'none',
  borderRadius: 'var(--radius-sm)',
  padding: '2px 8px',
  fontSize: '11px',
  cursor: 'pointer',
  marginRight: '4px',
};

const specHeadingStyle: React.CSSProperties = {
  margin: '12px 0 8px',
  fontSize: '12px',
  fontWeight: 600,
  color: 'var(--text-muted)',
};

const tdMono: React.CSSProperties = {
  padding: '8px 10px',
  verticalAlign: 'middle',
  fontFamily: 'var(--font-mono, monospace)',
  fontSize: '11px',
};

export default InstrumentsPage;
