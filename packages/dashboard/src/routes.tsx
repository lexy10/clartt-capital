import { createBrowserRouter, Navigate } from 'react-router-dom';
import { lazy, Suspense, type ComponentType } from 'react';
import AuthGuard from './components/AuthGuard';
import AdminRoute, { SuperAdminRoute } from './components/AdminRoute';
import LayoutShell from './components/LayoutShell';
import LoadingFallback from './components/LoadingFallback';

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

function withSuspense(Component: ComponentType) {
  return (
    <Suspense fallback={<LoadingFallback />}>
      <Component />
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
          { path: 'strategy', element: withSuspense(StrategiesPage) },
          { path: 'admin/instruments', element: withSuspense(InstrumentsPage) },
          { path: 'admin/algorithms', element: withSuspense(AlgorithmsPage) },
          { path: 'reconciliation', element: withSuspense(ReconciliationPage) },
          { path: 'events', element: withSuspense(EventTimeline) },
          { path: 'profile', element: withSuspense(ProfilePage) },
          // System pages are admin-only. The AdminRoute guard hard-bounces
          // non-admins back to "/" even if they hit the URLs directly.
          // User management is super-admin only; the ops pages are admin+.
          {
            element: <SuperAdminRoute />,
            children: [
              { path: 'admin/users', element: withSuspense(UsersPage) },
            ],
          },
          {
            element: <AdminRoute />,
            children: [
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
