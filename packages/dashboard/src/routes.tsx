import { createBrowserRouter, Navigate } from 'react-router-dom';
import { lazy, Suspense, type ComponentType } from 'react';
import AuthGuard from './components/AuthGuard';
import AdminRoute, { SuperAdminRoute } from './components/AdminRoute';
import LayoutShell from './components/LayoutShell';
import LoadingFallback from './components/LoadingFallback';
import { useAuthStore } from './stores/authStore';

const LoginPage = lazy(() => import('./components/LoginPage'));
const LiveDeskPage = lazy(() => import('./components/LiveDeskPage'));
// PerformancePage removed — its content was merged into LiveDeskPage.
const ChartPage = lazy(() => import('./components/ChartPage'));
const SignalsPage = lazy(() => import('./components/SignalsPage'));
const PositionsPage = lazy(() => import('./components/PositionsPage'));
const AccountsPage = lazy(() => import('./components/AccountsPage'));
const StrategiesPage = lazy(() => import('./components/StrategiesPage'));
const InstrumentsPage = lazy(() => import('./components/InstrumentsPage'));
const AlgorithmsPage = lazy(() => import('./components/AlgorithmsPage'));
const ReconciliationPage = lazy(() => import('./components/ReconciliationPage'));
const EventTimeline = lazy(() => import('./components/EventTimeline'));
const SystemHealthPanel = lazy(() => import('./components/SystemHealthPanel'));
const AgentsPage = lazy(() => import('./components/AgentsPage'));
const UsersPage = lazy(() => import('./components/UsersPage'));
const ProfilePage = lazy(() => import('./components/ProfilePage'));
const StrategyCatalog = lazy(() => import('./components/StrategyCatalog'));
const AlgorithmCatalog = lazy(() => import('./components/AlgorithmCatalog'));

function withSuspense(Component: ComponentType) {
  return (
    <Suspense fallback={<LoadingFallback />}>
      <Component />
    </Suspense>
  );
}

// Render the full management page for admins, or the read-only trader
// catalogue otherwise. Traders can view + backtest but not edit or see config.
function RoleView({ admin: Admin, trader: Trader }: { admin: ComponentType; trader: ComponentType }) {
  const role = useAuthStore((s) => s.currentUser?.role);
  const isAdmin = role === 'admin' || role === 'superadmin';
  const C = isAdmin ? Admin : Trader;
  return (
    <Suspense fallback={<LoadingFallback />}>
      <C />
    </Suspense>
  );
}

export const router = createBrowserRouter([
  {
    path: '/login',
    element: withSuspense(LoginPage),
  },
  {
    element: <AuthGuard />,
    children: [
      {
        element: <LayoutShell />,
        children: [
          { index: true, element: withSuspense(LiveDeskPage) },
          // Old /dashboard (PerformancePage) now redirects to Live Desk
          { path: 'dashboard', element: <Navigate to="/" replace /> },
          { path: 'live-desk', element: <Navigate to="/" replace /> },
          { path: 'chart', element: withSuspense(ChartPage) },
          { path: 'signals', element: withSuspense(SignalsPage) },
          { path: 'positions', element: withSuspense(PositionsPage) },
          { path: 'accounts', element: withSuspense(AccountsPage) },
          { path: 'reconciliation', element: withSuspense(ReconciliationPage) },
          { path: 'events', element: withSuspense(EventTimeline) },
          { path: 'profile', element: withSuspense(ProfilePage) },
          // Strategies + Algorithms: full management for admins, read-only
          // catalogue (view + backtest, no config/source) for traders.
          { path: 'strategy', element: <RoleView admin={StrategiesPage} trader={StrategyCatalog} /> },
          { path: 'admin/algorithms', element: <RoleView admin={AlgorithmsPage} trader={AlgorithmCatalog} /> },
          // User management is super-admin only.
          {
            element: <SuperAdminRoute />,
            children: [
              { path: 'admin/users', element: withSuspense(UsersPage) },
            ],
          },
          // Instruments + system pages stay admin-only.
          {
            element: <AdminRoute />,
            children: [
              { path: 'admin/instruments', element: withSuspense(InstrumentsPage) },
              { path: 'health', element: withSuspense(SystemHealthPanel) },
              { path: 'agents', element: withSuspense(AgentsPage) },
            ],
          },
          { path: '*', element: <Navigate to="/" replace /> },
        ],
      },
    ],
  },
]);
