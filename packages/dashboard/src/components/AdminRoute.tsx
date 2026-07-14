import { Navigate, Outlet } from 'react-router-dom';
import { useAuthStore } from '../stores/authStore';

/**
 * Route guards keyed on role.
 *
 * AdminRoute       → admin OR superadmin (ops pages: System Health, Agents).
 * SuperAdminRoute  → superadmin only (User Management).
 *
 * Wait for currentUser to load (render nothing while pending — the auth guard
 * already proved the token is valid), then allow or bounce to Live Desk.
 * Pairs with the Sidebar's `adminOnly` / `superAdminOnly` flags (which hide
 * the nav) for defense in depth against direct URL navigation.
 */
export default function AdminRoute() {
  const currentUser = useAuthStore((s) => s.currentUser);
  if (currentUser === null) return null;
  if (currentUser.role !== 'admin' && currentUser.role !== 'superadmin') {
    return <Navigate to="/" replace />;
  }
  return <Outlet />;
}

export function SuperAdminRoute() {
  const currentUser = useAuthStore((s) => s.currentUser);
  if (currentUser === null) return null;
  if (currentUser.role !== 'superadmin') {
    return <Navigate to="/" replace />;
  }
  return <Outlet />;
}
