import { type FC, useEffect, useState, useCallback, useRef } from 'react';
import { useAlgorithmStore } from '../stores/algorithmStore';
import type { AlgorithmInfo } from '../types/api';

function formatName(name: string): string {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

const AlgorithmsPage: FC = () => {
  const {
    algorithms, loading, error,
    selectedAlgorithm, source, sourceLoading,
    uploading, uploadError,
    saving, saveError,
    fetchAlgorithms, fetchSource, uploadAlgorithm, updateSource, deleteAlgorithm,
    selectAlgorithm, clearSource,
  } = useAlgorithmStore();

  const [deleting, setDeleting] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [editBuffer, setEditBuffer] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { fetchAlgorithms(); }, [fetchAlgorithms]);

  useEffect(() => {
    if (selectedAlgorithm) fetchSource(selectedAlgorithm);
  }, [selectedAlgorithm, fetchSource]);

  const handleUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    await uploadAlgorithm(file);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }, [uploadAlgorithm]);

  const handleDelete = useCallback(async (name: string) => {
    if (!confirm(`Delete algorithm "${formatName(name)}"? This cannot be undone.`)) return;
    setDeleting(name);
    try { await deleteAlgorithm(name); } catch { /* store handles */ }
    finally { setDeleting(null); }
  }, [deleteAlgorithm]);

  const handleEdit = useCallback(() => {
    if (source) {
      setEditBuffer(source.source);
      setEditing(true);
      setTimeout(() => textareaRef.current?.focus(), 50);
    }
  }, [source]);

  const handleCancelEdit = useCallback(() => {
    setEditing(false);
    setEditBuffer('');
  }, []);

  const handleSave = useCallback(async () => {
    if (!selectedAlgorithm || !editBuffer) return;
    const ok = await updateSource(selectedAlgorithm, editBuffer);
    if (ok) setEditing(false);
  }, [selectedAlgorithm, editBuffer, updateSource]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Ctrl/Cmd+S to save
    if ((e.metaKey || e.ctrlKey) && e.key === 's') {
      e.preventDefault();
      handleSave();
    }
    // Escape to cancel
    if (e.key === 'Escape') {
      handleCancelEdit();
    }
    // Tab inserts spaces
    if (e.key === 'Tab') {
      e.preventDefault();
      const ta = e.currentTarget;
      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      const val = ta.value;
      const newVal = val.substring(0, start) + '    ' + val.substring(end);
      setEditBuffer(newVal);
      setTimeout(() => { ta.selectionStart = ta.selectionEnd = start + 4; }, 0);
    }
  }, [handleSave, handleCancelEdit]);

  /* ── Detail view ── */
  if (selectedAlgorithm) {
    const alg = algorithms.find((a) => a.name === selectedAlgorithm);
    return (
      <div style={pageStyle}>
        <button onClick={() => { handleCancelEdit(); selectAlgorithm(null); clearSource(); }} style={backBtnStyle}>
          ← Back to Algorithms
        </button>

        {alg && (
          <div style={{ marginTop: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h2 style={{ fontSize: 18, fontWeight: 600, margin: '0 0 4px' }}>{formatName(alg.name)}</h2>
              <button
                onClick={() => handleDelete(alg.name)}
                disabled={deleting === alg.name}
                style={dangerBtnStyle}
              >
                {deleting === alg.name ? 'Deleting…' : 'Delete'}
              </button>
            </div>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '0 0 20px' }}>{alg.description}</p>

            {/* Default params */}
            <div style={sectionStyle}>
              <h3 style={sectionTitle}>Default Parameters</h3>
              {Object.entries(alg.default_params).map(([k, v]) => (
                <div key={k} style={rowStyle}>
                  <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{k.replace(/_/g, ' ')}</span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{String(v)}</span>
                </div>
              ))}
            </div>

            {/* Param schema */}
            <div style={sectionStyle}>
              <h3 style={sectionTitle}>Parameter Schema</h3>
              {(() => {
                const props = alg.param_schema?.properties;
                if (!props || typeof props !== 'object') return null;
                return Object.entries(props as Record<string, Record<string, unknown>>).map(([k, schema]) => (
                  <div key={k} style={rowStyle}>
                    <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
                      {k.replace(/_/g, ' ')}
                      {schema.description ? ` — ${String(schema.description)}` : ''}
                    </span>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
                      {String(schema.type)}
                      {schema.minimum != null ? ` [${String(schema.minimum)}` : ''}
                      {schema.maximum != null ? `–${String(schema.maximum)}]` : ''}
                    </span>
                  </div>
                ));
              })()}
            </div>

            {/* Source code */}
            <div style={sectionStyle}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <h3 style={{ ...sectionTitle, margin: 0 }}>Source Code</h3>
                {source && !editing && (
                  <button onClick={handleEdit} style={editBtnStyle}>Edit</button>
                )}
                {editing && (
                  <div style={{ display: 'flex', gap: 8 }}>
                    <span style={{ fontSize: 10, color: 'var(--text-muted)', alignSelf: 'center' }}>
                      Ctrl+S save · Esc cancel
                    </span>
                    <button onClick={handleCancelEdit} style={cancelBtnStyle}>Cancel</button>
                    <button onClick={handleSave} disabled={saving} style={saveBtnStyle}>
                      {saving ? 'Saving…' : 'Save'}
                    </button>
                  </div>
                )}
              </div>

              {saveError && (
                <div style={{ ...errorStyle, marginBottom: 12 }} role="alert">{saveError}</div>
              )}

              {sourceLoading ? (
                <div style={{ fontSize: 13, color: 'var(--text-secondary)', padding: 8 }}>Loading source…</div>
              ) : editing ? (
                <div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>
                    File: {source?.filename}
                  </div>
                  <textarea
                    ref={textareaRef}
                    value={editBuffer}
                    onChange={(e) => setEditBuffer(e.target.value)}
                    onKeyDown={handleKeyDown}
                    spellCheck={false}
                    style={editorStyle}
                    aria-label="Algorithm source code editor"
                  />
                </div>
              ) : source ? (
                <div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>
                    File: {source.filename}
                  </div>
                  <pre style={codeStyle}>{source.source}</pre>
                </div>
              ) : (
                <div style={{ fontSize: 13, color: 'var(--text-secondary)', padding: 8 }}>
                  Source not available (built-in algorithm)
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    );
  }

  /* ── List view ── */
  return (
    <div style={pageStyle}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>Algorithms</h1>
        <label style={uploadBtnStyle}>
          {uploading ? 'Uploading…' : '+ Upload Algorithm'}
          <input
            ref={fileInputRef}
            type="file"
            accept=".py"
            onChange={handleUpload}
            disabled={uploading}
            style={{ display: 'none' }}
          />
        </label>
      </div>

      {uploadError && (
        <div style={errorStyle} role="alert">{uploadError}</div>
      )}

      {error && (
        <div style={errorStyle} role="alert">
          <span>{error}</span>
          <button onClick={fetchAlgorithms} style={retryBtnStyle}>Retry</button>
        </div>
      )}

      {loading && algorithms.length === 0 && (
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', padding: 16 }}>Loading algorithms…</div>
      )}

      {!loading && !error && algorithms.length === 0 && (
        <div style={emptyStyle}>No algorithms registered</div>
      )}

      {algorithms.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 12 }}>
          {algorithms.map((alg) => (
            <AlgorithmCard
              key={alg.name}
              algorithm={alg}
              deleting={deleting === alg.name}
              onSelect={() => selectAlgorithm(alg.name)}
              onDelete={() => handleDelete(alg.name)}
            />
          ))}
        </div>
      )}
    </div>
  );
};

/* ── Card ── */

const AlgorithmCard: FC<{
  algorithm: AlgorithmInfo;
  deleting: boolean;
  onSelect: () => void;
  onDelete: () => void;
}> = ({ algorithm, deleting, onSelect, onDelete }) => (
  <div style={cardStyle}>
    <div
      onClick={onSelect}
      style={{ cursor: 'pointer', flex: 1 }}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter') onSelect(); }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>{formatName(algorithm.name)}</span>
        <span style={badgeStyle}>{algorithm.name}</span>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '0 0 12px', lineHeight: 1.4 }}>
        {algorithm.description}
      </p>
      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        {Object.keys(algorithm.default_params).length} parameter(s)
      </div>
    </div>
    <div style={{ borderTop: '1px solid var(--border-primary)', paddingTop: 10, marginTop: 10, display: 'flex', gap: 8 }}>
      <button onClick={onSelect} style={viewBtnStyle}>View Details</button>
      <button onClick={onDelete} disabled={deleting} style={deleteBtnStyle}>
        {deleting ? '…' : 'Delete'}
      </button>
    </div>
  </div>
);

/* ── Styles ── */

const pageStyle: React.CSSProperties = {
  padding: 24, fontFamily: 'var(--font-sans)', color: 'var(--text-primary)',
  background: 'var(--bg-primary)', minHeight: '100%',
};

const backBtnStyle: React.CSSProperties = {
  background: 'none', border: '1px solid var(--border-primary)',
  borderRadius: 'var(--radius-sm)', color: 'var(--text-secondary)',
  padding: '6px 12px', fontSize: 12, cursor: 'pointer',
};

const uploadBtnStyle: React.CSSProperties = {
  padding: '8px 16px', fontSize: 13, fontWeight: 500,
  background: 'var(--accent)', color: '#fff', border: 'none',
  borderRadius: 'var(--radius-sm)', cursor: 'pointer', display: 'inline-block',
};

const errorStyle: React.CSSProperties = {
  background: 'var(--danger-bg)', border: '1px solid var(--danger)',
  borderRadius: 'var(--radius-md)', padding: '8px 12px', marginBottom: 12,
  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  fontSize: 12, color: 'var(--danger)',
};

const retryBtnStyle: React.CSSProperties = {
  background: 'none', border: '1px solid var(--danger)',
  borderRadius: 'var(--radius-sm)', color: 'var(--danger)',
  padding: '4px 10px', fontSize: 11, cursor: 'pointer',
};

const emptyStyle: React.CSSProperties = {
  textAlign: 'center', padding: 48, color: 'var(--text-secondary)', fontSize: 14,
  background: 'var(--bg-surface)', borderRadius: 'var(--radius-sm)',
};

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)',
  padding: 16, border: '1px solid var(--border-primary)',
  display: 'flex', flexDirection: 'column',
};

const badgeStyle: React.CSSProperties = {
  fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)',
  background: 'var(--bg-primary)', padding: '2px 6px', borderRadius: 4,
};

const viewBtnStyle: React.CSSProperties = {
  flex: 1, padding: '5px 0', fontSize: 11, fontWeight: 500,
  background: 'var(--accent)', color: '#fff', border: 'none',
  borderRadius: 'var(--radius-sm)', cursor: 'pointer',
};

const deleteBtnStyle: React.CSSProperties = {
  padding: '5px 12px', fontSize: 11, fontWeight: 500,
  background: 'transparent', color: 'var(--danger, #ef4444)',
  border: '1px solid var(--danger, #ef4444)', borderRadius: 'var(--radius-sm)',
  cursor: 'pointer',
};

const sectionStyle: React.CSSProperties = {
  background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)',
  padding: 20, marginBottom: 16,
};

const sectionTitle: React.CSSProperties = {
  fontSize: 14, fontWeight: 600, margin: '0 0 12px',
};

const rowStyle: React.CSSProperties = {
  display: 'flex', justifyContent: 'space-between', padding: '6px 0',
  borderBottom: '1px solid var(--border-primary)',
};

const codeStyle: React.CSSProperties = {
  background: 'var(--bg-primary)', border: '1px solid var(--border-primary)',
  borderRadius: 'var(--radius-sm)', padding: 16, fontSize: 12,
  fontFamily: 'var(--font-mono)', overflow: 'auto', maxHeight: 500,
  whiteSpace: 'pre', lineHeight: 1.5, color: 'var(--text-primary)',
};

const dangerBtnStyle: React.CSSProperties = {
  padding: '6px 14px', fontSize: 12, fontWeight: 500,
  background: 'transparent', color: 'var(--danger, #ef4444)',
  border: '1px solid var(--danger, #ef4444)', borderRadius: 'var(--radius-sm)',
  cursor: 'pointer',
};

const editBtnStyle: React.CSSProperties = {
  padding: '5px 14px', fontSize: 11, fontWeight: 500,
  background: 'var(--accent)', color: '#fff', border: 'none',
  borderRadius: 'var(--radius-sm)', cursor: 'pointer',
};

const saveBtnStyle: React.CSSProperties = {
  padding: '5px 14px', fontSize: 11, fontWeight: 500,
  background: 'var(--success, #22c55e)', color: '#fff', border: 'none',
  borderRadius: 'var(--radius-sm)', cursor: 'pointer',
};

const cancelBtnStyle: React.CSSProperties = {
  padding: '5px 14px', fontSize: 11, fontWeight: 500,
  background: 'transparent', color: 'var(--text-secondary)',
  border: '1px solid var(--border-primary)', borderRadius: 'var(--radius-sm)',
  cursor: 'pointer',
};

const editorStyle: React.CSSProperties = {
  width: '100%', minHeight: 400, resize: 'vertical',
  background: 'var(--bg-primary)', border: '1px solid var(--accent, #3b82f6)',
  borderRadius: 'var(--radius-sm)', padding: 16, fontSize: 12,
  fontFamily: 'var(--font-mono)', color: 'var(--text-primary)',
  lineHeight: 1.5, tabSize: 4, outline: 'none',
  boxSizing: 'border-box',
};

export default AlgorithmsPage;
