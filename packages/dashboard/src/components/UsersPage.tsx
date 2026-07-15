import { type FC, useEffect, useState, useCallback } from 'react';
import { apiClient } from '../services/ApiClient';
import { useAuthStore } from '../stores/authStore';
import type { AdminUser } from '../types/api';

const ROLES = ['superadmin', 'admin', 'trader'] as const;

function emailToName(email: string): string {
  const local = (email.split('@')[0] || email).trim();
  return local.charAt(0).toUpperCase() + local.slice(1);
}

const UsersPage: FC = () => {
  const currentUser = useAuthStore((s) => s.currentUser);
  const refreshUsers = useAuthStore((s) => s.fetchCurrentUser);

  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [resetFor, setResetFor] = useState<AdminUser | null>(null);
  const [pendingRole, setPendingRole] = useState<{ user: AdminUser; role: string } | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setUsers(await apiClient.users.listAll());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load users');
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  // After a change that could affect the current admin or the switcher list,
  // refresh the authStore's cached user list too.
  const afterMutation = useCallback(async () => {
    await load();
    refreshUsers().catch(() => {});
  }, [load, refreshUsers]);

  // Selecting a new role opens a confirmation rather than applying instantly —
  // a role change is significant (grants/removes powers).
  const requestRole = (u: AdminUser, role: string) => {
    if (role !== u.role) setPendingRole({ user: u, role });
  };

  const confirmRole = async () => {
    if (!pendingRole) return;
    const { user: u, role } = pendingRole;
    setBusyId(u.id);
    setError(null);
    try {
      await apiClient.users.updateRole(u.id, role);
      await afterMutation();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update role');
    }
    setBusyId(null);
    setPendingRole(null);
  };

  const handleActive = async (u: AdminUser) => {
    setBusyId(u.id);
    setError(null);
    try {
      await apiClient.users.setActive(u.id, !u.isActive);
      await afterMutation();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update status');
    }
    setBusyId(null);
  };

  return (
    <div style={{ padding: 16, fontFamily: 'var(--font-sans)', color: 'var(--text-primary)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 16 }}>User Management</h2>
          <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--text-muted)' }}>
            Create users, assign roles, and enable or disable access.
          </p>
        </div>
        <button onClick={() => setShowCreate(true)} style={primaryBtn}>+ Add User</button>
      </div>

      {error && <div style={errorBanner} role="alert">{error}</div>}

      {loading ? (
        <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>Loading users…</p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ color: 'var(--text-muted)', textAlign: 'left' }}>
                <th style={th}>User</th>
                <th style={th}>Role</th>
                <th style={th}>Status</th>
                <th style={th}>Created</th>
                <th style={{ ...th, textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const isSelf = u.id === currentUser?.id;
                const busy = busyId === u.id;
                return (
                  <tr key={u.id} style={{ borderTop: '1px solid var(--border-primary)' }}>
                    <td style={td}>
                      <div style={{ fontWeight: 600 }}>
                        {emailToName(u.email)}
                        {isSelf && <span style={selfTag}>you</span>}
                      </div>
                      <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{u.email}</div>
                    </td>
                    <td style={td}>
                      <select
                        value={u.role}
                        disabled={busy}
                        onChange={(e) => requestRole(u, e.target.value)}
                        style={select}
                        title={isSelf && u.role === 'superadmin' ? "You can't remove your own super-admin role" : undefined}
                      >
                        {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    </td>
                    <td style={td}>
                      <span style={{ color: u.isActive ? 'var(--success)' : 'var(--text-muted)', fontSize: 11 }}>
                        {u.isActive ? '● Active' : '○ Disabled'}
                      </span>
                    </td>
                    <td style={{ ...td, color: 'var(--text-muted)', fontSize: 11 }}>
                      {new Date(u.createdAt).toLocaleDateString()}
                    </td>
                    <td style={{ ...td, textAlign: 'right', whiteSpace: 'nowrap' }}>
                      <button onClick={() => setResetFor(u)} disabled={busy} style={ghostBtn}>Reset password</button>
                      <button
                        onClick={() => handleActive(u)}
                        disabled={busy || isSelf}
                        style={u.isActive ? warnBtn : successBtn}
                        title={isSelf ? "You can't disable your own account" : undefined}
                      >
                        {u.isActive ? 'Disable' : 'Enable'}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {showCreate && (
        <CreateUserModal
          onClose={() => setShowCreate(false)}
          onCreated={async () => { setShowCreate(false); await afterMutation(); }}
        />
      )}
      {resetFor && (
        <ResetPasswordModal
          user={resetFor}
          onClose={() => setResetFor(null)}
          onDone={() => setResetFor(null)}
        />
      )}
      {pendingRole && (
        <Modal title="Change role" onClose={() => setPendingRole(null)}>
          <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            Change <strong>{pendingRole.user.email}</strong> from{' '}
            <span style={roleChip}>{pendingRole.user.role}</span> to{' '}
            <span style={roleChip}>{pendingRole.role}</span>?
            {pendingRole.role === 'superadmin' && (
              <><br /><span style={{ color: 'var(--warning)' }}>
                Super admins can impersonate any user and manage all users.
              </span></>
            )}
            {pendingRole.role === 'trader' && pendingRole.user.role !== 'trader' && (
              <><br /><span style={{ color: 'var(--text-muted)' }}>
                They will lose admin access.
              </span></>
            )}
          </p>
          <div style={modalFooter}>
            <button onClick={() => setPendingRole(null)} style={ghostBtn}>Cancel</button>
            <button onClick={confirmRole} disabled={busyId === pendingRole.user.id} style={primaryBtn}>
              {busyId === pendingRole.user.id ? 'Applying…' : 'Confirm change'}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
};

// ── Create user modal ──────────────────────────────────────────────────
const CreateUserModal: FC<{ onClose: () => void; onCreated: () => void }> = ({ onClose, onCreated }) => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<string>('trader');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setErr(null);
    if (!email.trim() || password.length < 8) { setErr('Email and a password of 8+ characters are required.'); return; }
    setSaving(true);
    try {
      await apiClient.users.create({ email: email.trim(), password, role });
      onCreated();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to create user');
      setSaving(false);
    }
  };

  return (
    <Modal title="Add User" onClose={onClose}>
      {err && <div style={errorBanner}>{err}</div>}
      <label style={label}>Email</label>
      <input style={input} type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="user@clarttcapital.com" />
      <label style={label}>Temporary password</label>
      <input style={input} type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters" autoComplete="new-password" />
      <label style={label}>Role</label>
      <select style={input} value={role} onChange={(e) => setRole(e.target.value)}>
        {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
      </select>
      <div style={modalFooter}>
        <button onClick={onClose} style={ghostBtn}>Cancel</button>
        <button onClick={submit} disabled={saving} style={primaryBtn}>{saving ? 'Creating…' : 'Create User'}</button>
      </div>
    </Modal>
  );
};

// ── Reset password modal ───────────────────────────────────────────────
const ResetPasswordModal: FC<{ user: AdminUser; onClose: () => void; onDone: () => void }> = ({ user, onClose, onDone }) => {
  const [password, setPassword] = useState('');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const submit = async () => {
    setErr(null);
    if (password.length < 8) { setErr('Password must be at least 8 characters.'); return; }
    setSaving(true);
    try {
      await apiClient.users.resetPassword(user.id, password);
      setDone(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to reset password');
      setSaving(false);
    }
  };

  return (
    <Modal title={`Reset password — ${user.email}`} onClose={onClose}>
      {done ? (
        <>
          <p style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            Password updated. Share the new password with the user securely — they should change it after logging in.
          </p>
          <div style={modalFooter}><button onClick={onDone} style={primaryBtn}>Done</button></div>
        </>
      ) : (
        <>
          {err && <div style={errorBanner}>{err}</div>}
          <label style={label}>New password</label>
          <input style={input} type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters" autoComplete="new-password" />
          <div style={modalFooter}>
            <button onClick={onClose} style={ghostBtn}>Cancel</button>
            <button onClick={submit} disabled={saving} style={primaryBtn}>{saving ? 'Saving…' : 'Set Password'}</button>
          </div>
        </>
      )}
    </Modal>
  );
};

const Modal: FC<{ title: string; onClose: () => void; children: React.ReactNode }> = ({ title, onClose, children }) => (
  <div style={overlay} onClick={onClose}>
    <div style={modalCard} onClick={(e) => e.stopPropagation()}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>{title}</h3>
        <button onClick={onClose} style={ghostBtn} aria-label="Close">✕</button>
      </div>
      {children}
    </div>
  </div>
);

// ── Styles ─────────────────────────────────────────────────────────────
const th: React.CSSProperties = { padding: '6px 8px', fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5, fontWeight: 600 };
const td: React.CSSProperties = { padding: '8px' };
const primaryBtn: React.CSSProperties = { background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)', padding: '6px 14px', fontSize: 12, fontWeight: 600, cursor: 'pointer' };
const ghostBtn: React.CSSProperties = { background: 'none', border: '1px solid var(--glass-border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-secondary)', padding: '4px 10px', fontSize: 11, cursor: 'pointer', marginLeft: 6 };
const warnBtn: React.CSSProperties = { ...ghostBtn, color: 'var(--warning)', borderColor: 'var(--warning)' };
const successBtn: React.CSSProperties = { ...ghostBtn, color: 'var(--success)', borderColor: 'var(--success)' };
const select: React.CSSProperties = { background: 'var(--bg-surface)', color: 'var(--text-primary)', border: '1px solid var(--glass-border)', borderRadius: 'var(--radius-sm)', fontSize: 11, padding: '4px 8px' };
const selfTag: React.CSSProperties = { marginLeft: 6, fontSize: 9, color: 'var(--accent)', border: '1px solid var(--border-glow)', borderRadius: 3, padding: '1px 5px' };
const roleChip: React.CSSProperties = { fontFamily: 'var(--font-mono)', fontSize: 11, padding: '1px 6px', borderRadius: 3, background: 'var(--bg-surface)', border: '1px solid var(--glass-border)' };
const errorBanner: React.CSSProperties = { background: 'var(--danger-bg, rgba(239,68,68,0.1))', border: '1px solid var(--danger)', borderRadius: 'var(--radius-sm)', padding: '8px 12px', marginBottom: 12, fontSize: 11, color: 'var(--danger)' };
const label: React.CSSProperties = { display: 'block', fontSize: 10, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.6, margin: '10px 0 4px' };
const input: React.CSSProperties = { width: '100%', background: 'var(--bg-surface)', border: '1px solid var(--glass-border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)', fontSize: 12, padding: '8px 10px', outline: 'none', boxSizing: 'border-box' };
const overlay: React.CSSProperties = { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 };
const modalCard: React.CSSProperties = { background: 'var(--bg-secondary)', border: '1px solid var(--glass-border)', borderRadius: 'var(--radius-lg)', padding: 20, width: '100%', maxWidth: 420 };
const modalFooter: React.CSSProperties = { display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16, paddingTop: 12, borderTop: '1px solid var(--glass-border)' };

export default UsersPage;
