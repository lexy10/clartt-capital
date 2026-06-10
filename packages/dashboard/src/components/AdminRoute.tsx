import { Navigate, Outlet } from 'react-router-dom';
import { useAuthStore } from '../stores/authStore';

/**
 * Route guard for admin-only pages (System Health, Agents).
 *
 * Behavior:
 *   - Wait for currentUser to load (renders nothing while pending — the auth
 *     guard already proved the token is valid).
 *   - Once loaded, admin → render the page; non-admin → redirect to Live Desk.
 *
 * Pair with the Sidebar's `adminOnly` flag (which hides the nav items) for
 * defense in depth: nav links don't render, AND direct URL navigation still
 * bounces non-admins back to safety.
 */
export default function AdminRoute() {
  const currentUser = useAuthStore((s) => s.currentUser);

  // Still loading the user profile — show nothing rather than flashing the
  // page contents for a frame.
  if (currentUser === null) {
    return null;
  }

  if (currentUser.role !== 'admin') {
    return <Navigate to="/" replace />;
  }

  return <Outlet />;
}
