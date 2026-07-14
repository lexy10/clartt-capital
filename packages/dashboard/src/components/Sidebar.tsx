import { type FC } from 'react';
import { NavLink } from 'react-router-dom';
import { useAuthStore } from '../stores/authStore';
import { ROUTES } from '../types/api';

const navSections = [
  {
    label: 'Overview',
    items: [
      {
        id: 'live-desk',
        path: ROUTES.LIVE_DESK,
        label: 'Live Desk',
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 19V5" />
            <path d="M4 12h16" />
            <path d="M8 19V9" />
            <path d="M12 19V5" />
            <path d="M16 19v-7" />
            <path d="M20 19V8" />
          </svg>
        ),
      },
      // Analytics moved into the Live Desk page — sidebar entry removed.
    ],
  },
  {
    label: 'Trading',
    items: [
      {
        id: 'chart',
        path: ROUTES.CHART,
        label: 'Chart',
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M6 20V14M12 20V4M18 20V10" />
          </svg>
        ),
      },
      {
        id: 'signals',
        path: ROUTES.SIGNALS,
        label: 'Signals',
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8Z" />
          </svg>
        ),
      },
      {
        id: 'positions',
        path: ROUTES.POSITIONS,
        label: 'Positions',
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 3h18v18H3z" />
            <path d="M3 9h18M9 3v18" />
          </svg>
        ),
      },
    ],
  },
  {
    label: 'Management',
    items: [
      {
        id: 'accounts',
        path: ROUTES.ACCOUNTS,
        label: 'Accounts',
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
            <circle cx="12" cy="7" r="4" />
          </svg>
        ),
      },
      {
        id: 'strategy',
        path: ROUTES.STRATEGY,
        label: 'Strategies',
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <path d="M12 6v6l4 2" />
          </svg>
        ),
      },
      {
        id: 'instruments',
        path: ROUTES.INSTRUMENTS,
        label: 'Instruments',
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 2L2 7l10 5 10-5-10-5Z" />
            <path d="M2 17l10 5 10-5" />
            <path d="M2 12l10 5 10-5" />
          </svg>
        ),
      },
      {
        id: 'algorithms',
        path: ROUTES.ALGORITHMS,
        label: 'Algorithms',
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
            <path d="M8 7h8M8 11h6" />
          </svg>
        ),
      },
      {
        id: 'reconciliation',
        path: ROUTES.RECONCILIATION,
        label: 'Reconciliation',
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
            <circle cx="9" cy="7" r="4" />
            <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
            <path d="M16 3.13a4 4 0 0 1 0 7.75" />
          </svg>
        ),
      },
      {
        id: 'events',
        path: ROUTES.EVENTS,
        label: 'Events',
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 8v4l3 3" />
            <circle cx="12" cy="12" r="10" />
          </svg>
        ),
      },
    ],
  },
  {
    label: 'System',
    adminOnly: true, // Hidden entirely for non-admins
    items: [
      {
        id: 'users',
        path: ROUTES.USERS,
        label: 'Users',
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
            <circle cx="9" cy="7" r="4" />
            <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
            <path d="M16 3.13a4 4 0 0 1 0 7.75" />
          </svg>
        ),
      },
      {
        id: 'health',
        path: ROUTES.HEALTH,
        label: 'System Health',
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
          </svg>
        ),
      },
      {
        id: 'agents',
        path: ROUTES.AGENTS,
        label: 'Agents',
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 2a4 4 0 0 1 4 4v1a4 4 0 0 1-8 0V6a4 4 0 0 1 4-4Z" />
            <path d="M6 21v-2a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v2" />
            <circle cx="12" cy="6" r="1" />
          </svg>
        ),
      },
    ],
  },
];

const Sidebar: FC = () => {
  const logout = useAuthStore((s) => s.logout);
  const currentUser = useAuthStore((s) => s.currentUser);
  const isAdmin = currentUser?.role === 'admin';

  // Hide entire sections marked adminOnly when the viewer isn't an admin.
  // System Health and Agents are debugging/ops pages — traders shouldn't see them.
  const visibleSections = navSections.filter((s) => !s.adminOnly || isAdmin);

  return (
    <nav className="sidebar-nav" role="navigation" aria-label="Main navigation">
      {/* Logo area */}
      <div className="sidebar-logo">
        <div className="sidebar-logo-icon">
          <span>CC</span>
        </div>
        <div className="sidebar-logo-text">
          <span className="sidebar-logo-title">Clartt Capital</span>
          <span className="sidebar-logo-subtitle">Trading Platform</span>
        </div>
      </div>

      {/* Nav sections */}
      <div className="sidebar-sections">
        {visibleSections.map((section) => (
          <div key={section.label} className="sidebar-section">
            <span className="sidebar-section-label">{section.label}</span>
            <ul className="sidebar-list">
              {section.items.map((item) => (
                <li key={item.id}>
                  <NavLink
                    to={item.path}
                    end={item.path === '/'}
                    className={({ isActive }) =>
                      `sidebar-item${isActive ? ' sidebar-item-active' : ''}`
                    }
                  >
                    <span className="sidebar-item-icon">{item.icon}</span>
                    <span className="sidebar-item-label">{item.label}</span>
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      {/* Bottom section */}
      <div className="sidebar-footer">
        <button
          onClick={logout}
          className="sidebar-item sidebar-logout"
          aria-label="Sign out"
        >
          <span className="sidebar-item-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <polyline points="16 17 21 12 16 7" />
              <line x1="21" y1="12" x2="9" y2="12" />
            </svg>
          </span>
          <span className="sidebar-item-label">Sign Out</span>
        </button>
      </div>
    </nav>
  );
};

export default Sidebar;
