import { type FC, useState } from 'react';
import { apiClient } from '../services/ApiClient';
import { useAuthStore } from '../stores/authStore';
import ThemePicker from './ThemePicker';

const ProfilePage: FC = () => {
  const currentUser = useAuthStore((s) => s.currentUser);
  const refreshUser = useAuthStore((s) => s.fetchCurrentUser);

  // Email edit
  const [email, setEmail] = useState(currentUser?.email ?? '');
  const [savingEmail, setSavingEmail] = useState(false);
  const [emailMsg, setEmailMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // Password change
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [savingPw, setSavingPw] = useState(false);
  const [pwMsg, setPwMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const saveEmail = async () => {
    setEmailMsg(null);
    if (!email.trim()) { setEmailMsg({ ok: false, text: 'Email is required.' }); return; }
    setSavingEmail(true);
    try {
      await apiClient.users.updateMe({ email: email.trim() });
      await refreshUser();
      setEmailMsg({ ok: true, text: 'Email updated.' });
    } catch (e) {
      setEmailMsg({ ok: false, text: e instanceof Error ? e.message : 'Failed to update email' });
    }
    setSavingEmail(false);
  };

  const changePassword = async () => {
    setPwMsg(null);
    if (newPassword.length < 8) { setPwMsg({ ok: false, text: 'New password must be at least 8 characters.' }); return; }
    if (newPassword !== confirmPassword) { setPwMsg({ ok: false, text: 'New passwords do not match.' }); return; }
    setSavingPw(true);
    try {
      await apiClient.users.changeMyPassword(currentPassword, newPassword);
      setPwMsg({ ok: true, text: 'Password changed.' });
      setCurrentPassword(''); setNewPassword(''); setConfirmPassword('');
    } catch (e) {
      setPwMsg({ ok: false, text: e instanceof Error ? e.message : 'Failed to change password' });
    }
    setSavingPw(false);
  };

  return (
    <div style={{ padding: 16, fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', maxWidth: 560 }}>
      <h2 style={{ margin: 0, fontSize: 16 }}>Profile</h2>
      <p style={{ margin: '2px 0 16px', fontSize: 12, color: 'var(--text-muted)' }}>
        Manage your account details and password.
      </p>

      {/* Account info */}
      <div style={card}>
        <div style={rowBetween}>
          <span style={muted}>Role</span>
          <span style={{ fontSize: 12, textTransform: 'capitalize' }}>{currentUser?.role ?? '—'}</span>
        </div>
      </div>

      {/* Appearance */}
      <div style={card}>
        <h3 style={cardTitle}>Appearance</h3>
        <p style={{ margin: '-4px 0 12px', fontSize: 11, color: 'var(--text-muted)' }}>
          Your theme is saved to your account and follows you on this browser.
        </p>
        <ThemePicker />
      </div>

      {/* Email */}
      <div style={card}>
        <h3 style={cardTitle}>Email</h3>
        {emailMsg && <div style={msgStyle(emailMsg.ok)}>{emailMsg.text}</div>}
        <label style={label}>Email address</label>
        <input style={input} type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        <div style={footer}>
          <button onClick={saveEmail} disabled={savingEmail || email === currentUser?.email} style={primaryBtn}>
            {savingEmail ? 'Saving…' : 'Save Email'}
          </button>
        </div>
      </div>

      {/* Password */}
      <div style={card}>
        <h3 style={cardTitle}>Change Password</h3>
        {pwMsg && <div style={msgStyle(pwMsg.ok)}>{pwMsg.text}</div>}
        <label style={label}>Current password</label>
        <input style={input} type="password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} autoComplete="current-password" />
        <label style={label}>New password</label>
        <input style={input} type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="At least 8 characters" autoComplete="new-password" />
        <label style={label}>Confirm new password</label>
        <input style={input} type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} autoComplete="new-password" />
        <div style={footer}>
          <button onClick={changePassword} disabled={savingPw || !currentPassword || !newPassword} style={primaryBtn}>
            {savingPw ? 'Saving…' : 'Change Password'}
          </button>
        </div>
      </div>
    </div>
  );
};

const card: React.CSSProperties = { background: 'var(--bg-secondary)', border: '1px solid var(--glass-border)', borderRadius: 'var(--radius-md)', padding: 16, marginBottom: 14 };
const cardTitle: React.CSSProperties = { margin: '0 0 10px', fontSize: 13, fontWeight: 600 };
const rowBetween: React.CSSProperties = { display: 'flex', justifyContent: 'space-between', alignItems: 'center' };
const muted: React.CSSProperties = { fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5 };
const label: React.CSSProperties = { display: 'block', fontSize: 10, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.6, margin: '10px 0 4px' };
const input: React.CSSProperties = { width: '100%', background: 'var(--bg-surface)', border: '1px solid var(--glass-border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)', fontSize: 12, padding: '8px 10px', outline: 'none', boxSizing: 'border-box' };
const footer: React.CSSProperties = { display: 'flex', justifyContent: 'flex-end', marginTop: 14 };
const primaryBtn: React.CSSProperties = { background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)', padding: '6px 14px', fontSize: 12, fontWeight: 600, cursor: 'pointer' };
const msgStyle = (ok: boolean): React.CSSProperties => ({
  fontSize: 11, padding: '8px 12px', marginBottom: 10, borderRadius: 'var(--radius-sm)',
  color: ok ? 'var(--success)' : 'var(--danger)',
  background: ok ? 'rgba(63,185,80,0.1)' : 'var(--danger-bg, rgba(239,68,68,0.1))',
  border: `1px solid ${ok ? 'var(--success)' : 'var(--danger)'}`,
});

export default ProfilePage;
