import { useEffect, useMemo } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Layout from './Layout';
import Sidebar from './Sidebar';
import TopBar from './TopBar';
import InfoPane from './InfoPane';
import ControlTower from './ControlTower';
import { useChartStore } from '../stores/chartStore';
import { useAccountStore } from '../stores/accountStore';
import { usePerformanceStore } from '../stores/performanceStore';
import { useAuthStore } from '../stores/authStore';
import { wsManager } from '../services/WebSocketManager';
import { ROUTES } from '../types/api';

export default function LayoutShell() {
  const { pathname } = useLocation();
  const instrument = useChartStore((s) => s.instrument);
  const viewingAsUserId = useAuthStore((s) => s.viewingAsUserId);

  const isChartRoute = pathname === ROUTES.CHART;
  const isChartFullscreen = useChartStore((s) => s.isFullscreen);

  // When the admin switches "viewing as" via the top-bar dropdown, we want
  // the page body + right rail to fully remount so every stateful component
  // re-fetches against the new asUserId. TopBar (and the dropdown inside
  // it) stays mounted — no flicker on the very control the user just clicked.
  // The 'self' fallback makes the key stable when no impersonation is active.
  const scopeKey = viewingAsUserId ?? 'self';

  const panelContext = useMemo(() => {
    if (pathname === '/' || pathname === '/live-desk') return 'live-desk' as const;
    if (pathname === '/dashboard') return 'analytics' as const;
    if (pathname === '/chart') return 'chart' as const;
    if (pathname === '/strategy') return 'strategies' as const;
    if (pathname === '/accounts') return 'accounts' as const;
    if (pathname === '/signals') return 'signals' as const;
    if (pathname === '/positions') return 'positions' as const;
    if (pathname === '/events') return 'events' as const;
    if (pathname === '/reconciliation') return 'reconciliation' as const;
    if (pathname.startsWith('/admin/')) return 'admin' as const;
    if (pathname === '/agents') return 'agents' as const;
    if (pathname === '/health') return 'system' as const;
    return 'default' as const;
  }, [pathname]);

  useEffect(() => {
    wsManager.connect();
    useAccountStore.getState().subscribeToSync();
    usePerformanceStore.getState().subscribeToSync();
    return () => {
      useAccountStore.getState().unsubscribeFromSync();
      usePerformanceStore.getState().unsubscribeFromSync();
      wsManager.disconnect();
    };
  }, []);

  return (
    <Layout
      sidebar={<Sidebar />}
      topBar={<TopBar />}
      chartPane={<div key={scopeKey} style={{ height: '100%' }}><Outlet /></div>}
      infoPane={isChartRoute ? <InfoPane activeView="chart" instrument={instrument} /> : <></>}
      rightPanel={<ControlTower key={scopeKey} context={panelContext} />}
      isChartFullscreen={isChartRoute ? isChartFullscreen : true}
    />
  );
}
