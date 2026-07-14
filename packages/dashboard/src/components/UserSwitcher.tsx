import { type FC } from 'react';
import { useAuthStore } from '../stores/authStore';
import { usePerformanceStore } from '../stores/performanceStore';
import { useAccountStore } from '../stores/accountStore';

/**
 * Admin-only user impersonation dropdown.
 *
 * Lives on the RIGHT side of the top bar. Lets the super admin view the
 * dashboard as any user — accounts, trades, positions, performance all
 * re-scope to the selected user's data. Normal users (role !== 'admin')
 * see nothing here.
 *
 * Selection persists across reloads via authStore.viewingAsUserId (saved
 * to localStorage). The actual data-scoping happens in the ApiClient
 * interceptor: whenever viewingAsUserId is set AND the caller is admin,
 * requests get an `asUserId` query param the backend honors.
 */
const UserSwitcher: FC = () => {
  const currentUser = useAuthStore((s) => s.currentUser);
  const allUsers = useAuthStore((s) => s.allUsers);
  const viewingAsUserId = useAuthStore((s) => s.viewingAsUserId);
  const setViewingAsUserId = useAuthStore((s) => s.setViewingAsUserId);

  // Impersonation ("Viewing as") is a super-admin-only power.
  if (!currentUser || currentUser.role !== 'superadmin' || allUsers.length === 0) {
    return null;
  }

  // Sort the dropdown: self first, then everyone else alphabetically.
  const sorted = [...allUsers].sort((a, b) => {
    if (a.id === currentUser.id) return -1;
    if (b.id === currentUser.id) return 1;
    return a.email.localeCompare(b.email);
  });

  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Viewing:</span>
      <select
        aria-label="View dashboard as user"
        value={viewingAsUserId ?? currentUser.id}
        onChange={(e) => {
          const v = e.target.value;
          const next = !v || v === currentUser.id ? null : v;
          // Don't trigger remount on no-op selection
          if (next === viewingAsUserId) return;
          // Wipe the cached user-scoped data BEFORE flipping viewingAsUserId.
          // Without this, the about-to-remount components find the previous
          // user's data still in the store on their first render and the
          // sticky stale-while-revalidate refs latch onto it, so the switch
          // looks like nothing changed. Clearing first guarantees the
          // remount starts from empty → skeletons show → real data arrives.
          usePerformanceStore.setState({
            overview: null,
            overviewLoading: true,
            accounts: [],
            accountsLoading: true,
            activityFeed: [],
            drillDown: null,
          });
          useAccountStore.setState({
            accounts: [],
            accountDetails: {},
            accountStatuses: {},
          });
          setViewingAsUserId(next);
          // LayoutShell's scopeKey now changes → ControlTower + page body
          // remount → their useEffect-driven fetches re-run with the new
          // asUserId param via the ApiClient interceptor.
        }}
        style={{
          background: 'var(--bg-surface)',
          color: 'var(--text-primary)',
          border: '1px solid var(--border-primary)',
          borderRadius: 'var(--radius-sm)',
          fontSize: 11,
          padding: '4px 8px',
          maxWidth: 220,
        }}
      >
        {sorted.map((u) => {
          const isYou = u.id === currentUser.id;
          const label = emailToName(u.email);
          return (
            <option key={u.id} value={u.id}>
              {label}{isYou ? ' (you)' : ''}{!isYou && u.role !== 'trader' ? ` · ${u.role}` : ''}
            </option>
          );
        })}
      </select>
    </div>
  );
};

function emailToName(email: string): string {
  const local = (email.split('@')[0] || email).trim();
  return local.charAt(0).toUpperCase() + local.slice(1);
}

export default UserSwitcher;
