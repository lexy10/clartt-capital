import { type FC, useEffect, useState } from 'react';
import { useWatchlistStore } from '../stores/watchlistStore';

const WatchlistManager: FC = () => {
  const {
    watchlists,
    loading,
    error,
    fetchWatchlists,
    createWatchlist,
    updateWatchlist,
    deleteWatchlist,
  } = useWatchlistStore();

  const [newName, setNewName] = useState('');
  const [newInstruments, setNewInstruments] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState('');
  const [editInstruments, setEditInstruments] = useState('');

  useEffect(() => {
    fetchWatchlists();
  }, [fetchWatchlists]);

  const handleCreate = async () => {
    const name = newName.trim();
    if (!name) return;
    const instruments = newInstruments
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    await createWatchlist({ name, instruments });
    setNewName('');
    setNewInstruments('');
  };

  const startEdit = (id: string, name: string, instruments: string[]) => {
    setEditingId(id);
    setEditName(name);
    setEditInstruments(instruments.join(', '));
  };

  const handleUpdate = async () => {
    if (!editingId) return;
    const instruments = editInstruments
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    await updateWatchlist(editingId, { name: editName, instruments });
    setEditingId(null);
  };

  return (
    <div style={{ padding: '12px', fontFamily: 'var(--font-sans)', color: 'var(--text-primary)' }}>
      <h3 style={{ margin: '0 0 12px', fontSize: '14px', color: 'var(--text-secondary)' }}>
        Watchlists
      </h3>

      {error && (
        <p style={{ color: 'var(--danger)', fontSize: '12px', margin: '0 0 8px' }}>{error}</p>
      )}

      {/* Create form */}
      <div style={{ display: 'flex', gap: '6px', marginBottom: '12px', flexWrap: 'wrap' }}>
        <input
          type="text"
          placeholder="Name"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          style={inputStyle}
          aria-label="Watchlist name"
        />
        <input
          type="text"
          placeholder="Instruments (comma-separated)"
          value={newInstruments}
          onChange={(e) => setNewInstruments(e.target.value)}
          style={{ ...inputStyle, flex: 1 }}
          aria-label="Instruments"
        />
        <button onClick={handleCreate} disabled={loading || !newName.trim()} style={btnStyle}>
          Add
        </button>
      </div>

      {loading && <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Loading…</p>}

      {!loading && watchlists.length === 0 && (
        <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>No watchlists</p>
      )}

      {watchlists.map((wl) => (
        <div
          key={wl.id}
          style={{
            background: 'var(--bg-surface)',
            borderRadius: 'var(--radius-sm)',
            padding: '8px 10px',
            marginBottom: '8px',
          }}
        >
          {editingId === wl.id ? (
            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
              <input
                type="text"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                style={inputStyle}
                aria-label="Edit watchlist name"
              />
              <input
                type="text"
                value={editInstruments}
                onChange={(e) => setEditInstruments(e.target.value)}
                style={{ ...inputStyle, flex: 1 }}
                aria-label="Edit instruments"
              />
              <button onClick={handleUpdate} style={btnStyle}>Save</button>
              <button onClick={() => setEditingId(null)} style={btnSecondaryStyle}>Cancel</button>
            </div>
          ) : (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontWeight: 600, fontSize: '13px' }}>{wl.name}</span>
                <div style={{ display: 'flex', gap: '4px' }}>
                  <button
                    onClick={() => startEdit(wl.id, wl.name, wl.instruments)}
                    style={btnSmallStyle}
                    aria-label={`Edit ${wl.name}`}
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => deleteWatchlist(wl.id)}
                    style={{ ...btnSmallStyle, color: 'var(--danger)' }}
                    aria-label={`Delete ${wl.name}`}
                  >
                    Delete
                  </button>
                </div>
              </div>
              {wl.instruments.length > 0 && (
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>
                  {wl.instruments.join(', ')}
                </div>
              )}
            </>
          )}
        </div>
      ))}
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

const btnSecondaryStyle: React.CSSProperties = {
  ...btnStyle,
  background: 'var(--bg-tertiary)',
};

const btnSmallStyle: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: 'var(--text-secondary)',
  fontSize: '11px',
  cursor: 'pointer',
  padding: '2px 4px',
};

export default WatchlistManager;
