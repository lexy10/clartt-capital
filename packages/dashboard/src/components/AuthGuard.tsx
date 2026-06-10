import { useEffect } from 'react';
import { Navigate, Outlet } from 'react-router-dom';
import { useAuthStore } from '../stores/authStore';

export default function AuthGuard() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const currentUser = useAuthStore((s) => s.currentUser);
  const fetchCurrentUser = useAuthStore((s) => s.fetchCurrentUser);

  // Lazy-load the current user on first authenticated render. Fires once;
  // re-runs only if logout clears currentUser.
  useEffect(() => {
    if (isAuthenticated && !currentUser) {
      fetchCurrentUser();
    }
  }, [isAuthenticated, currentUser, fetchCurrentUser]);

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}
