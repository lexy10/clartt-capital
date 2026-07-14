import { type FC, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useAuthStore } from '../stores/authStore';
import { ROUTES } from '../types/api';

/**
 * Top-bar identity widget on the LEFT side.
 *
 * - Admins get a friendly greeting: "Welcome, Trader1".
 * - Other users get just their name (no greeting — keeps the bar uncluttered
 *   for the day-to-day trader UI).
 *
 * No interactivity here. Account-scope switching lives in
 * AccountSwitcher.tsx on the right side.
 */
const UserGreeting: FC = () => {
  const currentUser = useAuthStore((s) => s.currentUser);

  const displayName = useMemo(
    () => emailToName(currentUser?.email ?? ''),
    [currentUser?.email],
  );

  if (!currentUser) return null;

  const isAdmin = currentUser.role === 'admin' || currentUser.role === 'superadmin';

  return (
    <Link
      to={ROUTES.PROFILE}
      title="Profile & password"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        fontSize: 13,
        textDecoration: 'none',
      }}
    >
      <Avatar name={displayName} />
      {isAdmin ? (
        <span style={{ color: 'var(--text-secondary)' }}>
          Welcome, <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{displayName}</span>
        </span>
      ) : (
        <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{displayName}</span>
      )}
    </Link>
  );
};

function emailToName(email: string): string {
  if (!email) return '';
  const local = email.split('@')[0] || email;
  return local.charAt(0).toUpperCase() + local.slice(1);
}

const Avatar: FC<{ name: string }> = ({ name }) => {
  const initial = (name[0] || '?').toUpperCase();
  return (
    <span
      aria-hidden="true"
      style={{
        width: 24,
        height: 24,
        borderRadius: '50%',
        background: 'var(--bg-surface)',
        border: '1px solid var(--border-primary)',
        color: 'var(--text-primary)',
        fontSize: 11,
        fontWeight: 600,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      {initial}
    </span>
  );
};

export default UserGreeting;
