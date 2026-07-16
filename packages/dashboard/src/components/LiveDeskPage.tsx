import { type FC, useEffect, useMemo, useRef, useState } from 'react';
import ReconciliationNotificationBanner from './ReconciliationNotificationBanner';
import { DetailLink } from './operations/OpsHelpers';
// Analytics components moved in from the deleted PerformancePage
import TimePeriodSelector from './performance/TimePeriodSelector';
import MultiAccountSummary from './performance/MultiAccountSummary';
import AccountPerformanceCard from './performance/AccountPerformanceCard';
import TradeDrillDown from './performance/TradeDrillDown';
import Skeleton from './Skeleton';
import { apiClient } from '../services/ApiClient';
import { wsManager } from '../services/WebSocketManager';
import { useHealthStore } from '../stores/healthStore';
import { usePerformanceStore } from '../stores/performanceStore';
import { usePortfolioStore } from '../stores/portfolioStore';
import { useReconciliationStore } from '../stores/reconciliationStore';
import type { Signal } from '../types/signal';
import { signalStrategyName } from '../types/signal';
import SignalStatusBadge from './SignalStatusBadge';
import { ROUTES } from '../types/api';

type Tone = 'success' | 'warning' | 'danger' | 'muted';

const moneyFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 2,
});

function formatMoney(value: number): string {
  return moneyFormatter.format(value);
}

function pnlTone(value: number): Tone {
  if (value > 0) return 'success';
  if (value < 0) return 'danger';
  return 'muted';
}

/** Translate a signal's raw metadata into plain English a trader can read.
 *  Skips noise: empty values, "unknown" session, zero-spread placeholders. */
function signalReason(signal: Signal): string {
  const metadata = (signal.metadata ?? {}) as Record<string, unknown>;
  const parts: string[] = [];

  // Setup type — the structural read
  if (metadata.bos_type) {
    const dir = String(metadata.bos_type).toLowerCase() === 'bullish' ? 'Bullish' : 'Bearish';
    parts.push(`${dir} structure break`);
  }
  if (metadata.liquidity_swept) {
    parts.push('liquidity swept');
  }

  // Timing — stochastic / momentum
  if (metadata.stoch_state) {
    const state = String(metadata.stoch_state).toLowerCase();
    if (state === 'oversold') parts.push('momentum: oversold');
    else if (state === 'overbought') parts.push('momentum: overbought');
  }

  // Volatility context (only if it's actually meaningful, not 1.00x)
  if (metadata.volatility_ratio != null) {
    const v = Number(metadata.volatility_ratio);
    if (v > 0 && Math.abs(v - 1) > 0.1) {
      const label = v >= 1.5 ? 'high vol'
                  : v <= 0.5 ? 'low vol'
                  : `${v.toFixed(2)}× vol`;
      parts.push(label);
    }
  }

  // Session (skip the literal string "unknown")
  if (metadata.session && String(metadata.session).toLowerCase() !== 'unknown') {
    parts.push(`${metadata.session} session`);
  }

  // Spread — only if it's nonzero (zero usually means "no spread data")
  if (metadata.spread_at_generation != null && Number(metadata.spread_at_generation) > 0) {
    parts.push(`spread ${Number(metadata.spread_at_generation).toFixed(1)}`);
  }

  if (parts.length === 0) {
    return signal.strategy_id ? `Strategy ${signal.strategy_id.slice(0, 8)}` : 'Algorithmic signal';
  }
  // First word capitalized, gentle separator
  parts[0] = parts[0].charAt(0).toUpperCase() + parts[0].slice(1);
  return parts.join(' · ');
}

// StatusBadge, DetailLink, MetricTile, HealthSummaryCard, ReconciliationSummaryCard
// moved to components/operations/ — the right rail (ControlTower) owns them now.

const PositionsPanel: FC = () => {
  const positions = usePortfolioStore((s) => s.positions);
  const accounts = usePerformanceStore((s) => s.accounts);
  const labels = useMemo(() => new Map(accounts.map((account) => [account.accountId, account.accountLabel])), [accounts]);

  return (
    <div className="live-desk-card">
      <div className="live-desk-card-header">
        <span>Open Positions</span>
        <DetailLink to={ROUTES.POSITIONS}>All positions</DetailLink>
      </div>
      {positions.length === 0 ? (
        <div className="live-desk-empty">No open positions</div>
      ) : (
        <div className="live-desk-list">
          {positions.slice(0, 6).map((position) => (
            <div className="live-desk-list-row" key={position.id}>
              <div>
                <strong>{position.instrument}</strong>
                <span>{labels.get(position.account_id) ?? position.account_id.slice(0, 8)}</span>
              </div>
              <div>
                <strong className={position.direction === 'BUY' ? 'live-desk-text-success' : 'live-desk-text-danger'}>
                  {position.direction}
                </strong>
                <span className={pnlTone(position.unrealized_pnl ?? 0) === 'danger' ? 'live-desk-text-danger' : pnlTone(position.unrealized_pnl ?? 0) === 'success' ? 'live-desk-text-success' : ''}>
                  {formatMoney(position.unrealized_pnl ?? 0)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

/** Latest signals across ALL instruments — independent of the chart selection.
 *  Shows the strategy that generated each signal and whether it actually
 *  traded (or why it didn't). */
const LatestSignalsPanel: FC = () => {
  const [signals, setSignals] = useState<Signal[]>([]);

  useEffect(() => {
    let cancelled = false;
    apiClient.signals.getRecent({ limit: 8 })
      .then((data) => {
        if (!cancelled) setSignals(data);
      })
      .catch(() => {
        if (!cancelled) setSignals([]);
      });

    // Accept signals for every instrument — no chart-instrument filter.
    const subId = wsManager.subscribe('signals', (signal: Signal) => {
      setSignals((prev) => [signal, ...prev.filter((item) => item.id !== signal.id)].slice(0, 8));
    });

    return () => {
      cancelled = true;
      wsManager.unsubscribe(subId);
    };
  }, []);

  return (
    <div className="live-desk-card">
      <div className="live-desk-card-header">
        <span>Latest Signals</span>
        <DetailLink to={ROUTES.SIGNALS}>Signals</DetailLink>
      </div>
      {signals.length === 0 ? (
        <div className="live-desk-empty">No recent signals</div>
      ) : (
        <div className="live-desk-list">
          {signals.slice(0, 5).map((signal) => (
            <div className="live-desk-signal" key={signal.id}>
              <div className="live-desk-signal-head">
                <strong className={signal.direction === 'BUY' ? 'live-desk-text-success' : 'live-desk-text-danger'}>
                  {signal.direction} {signal.instrument}
                </strong>
                <SignalStatusBadge signal={signal} />
              </div>
              <div className="live-desk-signal-prices">
                <span>E {signal.entry_price.toFixed(1)}</span>
                <span>SL {signal.stop_loss.toFixed(1)}</span>
                <span>TP {signal.take_profit.toFixed(1)}</span>
              </div>
              <p>
                <span style={{ color: 'var(--text-secondary)', fontWeight: 600 }}>{signalStrategyName(signal)}</span>
                {' · '}{signalReason(signal)}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const LiveDeskPage: FC = () => {
  const fetchOverview = usePerformanceStore((s) => s.fetchOverview);
  const fetchAccounts = usePerformanceStore((s) => s.fetchAccounts);
  const fetchActivityFeed = usePerformanceStore((s) => s.fetchActivityFeed);
  const fetchPositions = usePortfolioStore((s) => s.fetchPositions);
  const fetchHealthSnapshot = useHealthStore((s) => s.fetchHealthSnapshot);
  const fetchReports = useReconciliationStore((s) => s.fetchReports);
  const subscribeToAlerts = useReconciliationStore((s) => s.subscribeToAlerts);
  const unsubscribeFromAlerts = useReconciliationStore((s) => s.unsubscribeFromAlerts);

  useEffect(() => {
    // Initial fetches — the right rail's KillSwitch/Autopilot/Health/Reconciliation
    // widgets read these same stores, so we keep them warm here too.
    fetchOverview();
    fetchAccounts();
    fetchActivityFeed();
    fetchPositions();
    fetchHealthSnapshot();
    fetchReports({ page: 1 });
    subscribeToAlerts();

    // Silent background refresh — never flash skeletons on the 30s tick,
    // even when accounts/overview are empty. The user already saw the
    // initial skeleton; subsequent refreshes should just swap data in place.
    const refresh = window.setInterval(() => {
      fetchOverview(true);
      fetchAccounts(true);
      fetchPositions();
      fetchHealthSnapshot();
      fetchReports({ page: 1 });
    }, 30000);

    return () => {
      window.clearInterval(refresh);
      unsubscribeFromAlerts();
    };
  }, [
    fetchAccounts,
    fetchActivityFeed,
    fetchHealthSnapshot,
    fetchOverview,
    fetchPositions,
    fetchReports,
    subscribeToAlerts,
    unsubscribeFromAlerts,
  ]);

  return (
    <div className="live-desk">
      <ReconciliationNotificationBanner />

      {/* Hero status row + Open/Unrealized/Open-Exposure tiles removed —
       *  those signals live in the right rail (Kill Switch / Portfolio /
       *  System) and inside the per-account cards. */}

      {/* ── Multi-account summary + per-account cards + drill-down ── */}
      <AnalyticsSection />

      {/* ── Live trading panels (signals + positions, no overlay) ── */}
      <section className="live-desk-workspace" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <LatestSignalsPanel />
        <PositionsPanel />
      </section>
    </div>
  );
};

/** Analytics + per-account trade history with date period filters,
 *  moved in from the now-removed PerformancePage. */
const AnalyticsSection: FC = () => {
  const fetchOverview = usePerformanceStore((s) => s.fetchOverview);
  const fetchAccounts = usePerformanceStore((s) => s.fetchAccounts);
  const fetchDrillDown = usePerformanceStore((s) => s.fetchDrillDown);
  const closeDrillDown = usePerformanceStore((s) => s.closeDrillDown);
  const accounts = usePerformanceStore((s) => s.accounts);
  const drillDown = usePerformanceStore((s) => s.drillDown);
  const drillDownLoading = usePerformanceStore((s) => s.drillDownLoading);
  const drillDownError = usePerformanceStore((s) => s.drillDownError);
  const accountsLoading = usePerformanceStore((s) => s.accountsLoading);
  const positions = usePortfolioStore((s) => s.positions);

  useEffect(() => {
    fetchOverview();
    fetchAccounts();
  }, [fetchOverview, fetchAccounts]);

  // Group open positions by account so each card can show its own live trades.
  const positionsByAccount = useMemo(() => {
    const map = new Map<string, typeof positions>();
    for (const p of positions) {
      const list = map.get(p.account_id) ?? [];
      list.push(p);
      map.set(p.account_id, list);
    }
    return map;
  }, [positions]);

  // Only surface accounts that are actually relevant to the selected period:
  // either ran trades during it, or have something open right now. Idle
  // accounts (e.g. configured but no activity yet) are hidden — the user
  // doesn't need to look at them on the live desk.
  const activeAccounts = useMemo(
    () => accounts.filter((a) => {
      const open = (positionsByAccount.get(a.accountId)?.length ?? 0) > 0;
      return a.totalTrades > 0 || (a.openPositionsCount ?? 0) > 0 || open;
    }),
    [accounts, positionsByAccount],
  );

  // No sticky stale-while-revalidate here: period dropdown changes are
  // silent (loading flag doesn't flip), so empty results from the filter
  // are authoritative — we want them to clear the grid immediately. The
  // earlier sticky ref was hiding the filter result behind a stale snapshot
  // from the previous period.
  const visibleAccounts = activeAccounts;
  // Whether the user has accounts at all (even idle ones) — drives which
  // empty-state message we render below.
  const hasAnyAccounts = accounts.length > 0;

  // Track whether we've completed at least one fetch. Before that — on the
  // very first render, when the store is still at its default empty state
  // and the useEffect above hasn't yet kicked the fetch — we'd otherwise
  // flash the "No accounts yet" message for one frame. Show skeletons until
  // we've actually seen the loading flag toggle.
  const hasFetchedOnceRef = useRef(false);
  if (accountsLoading || accounts.length > 0) {
    hasFetchedOnceRef.current = true;
  }

  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '0 4px',
      }}>
        <h2 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
          Trades & Performance
        </h2>
        <TimePeriodSelector />
      </div>

      <MultiAccountSummary />

      {/* Empty-state branches:
       *   1. Still on the initial fetch → skeletons (never flash a message)
       *   2. Has accounts but none with activity in this period → "No activity"
       *   3. Truly no accounts → "add one from the Accounts page"
       *   Otherwise: real cards.                                            */}
      {visibleAccounts.length === 0 ? (
        accountsLoading || !hasFetchedOnceRef.current ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 12 }}>
            {[0, 1, 2].map((i) => (
              <AccountCardSkeleton key={i} />
            ))}
          </div>
        ) : hasAnyAccounts ? (
          <div style={{ padding: 16, fontSize: 12, color: 'var(--text-muted)' }}>
            No account activity in the selected period.
          </div>
        ) : (
          <div style={{ padding: 16, fontSize: 12, color: 'var(--text-muted)' }}>
            No accounts yet — add one from the Accounts page.
          </div>
        )
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 12 }}>
          {visibleAccounts.map((acc) => (
            <AccountPerformanceCard
              key={acc.accountId}
              data={acc}
              openPositions={positionsByAccount.get(acc.accountId) ?? []}
              onDrillDown={(id) => fetchDrillDown(id)}
            />
          ))}
        </div>
      )}

      {drillDown && drillDown.trades && (
        <TradeDrillDown
          trades={drillDown.trades}
          onBack={closeDrillDown}
        />
      )}
      {drillDownLoading && (
        <div style={{ padding: 12, fontSize: 12, color: 'var(--text-muted)' }}>Loading trade detail…</div>
      )}
      {drillDownError && (
        <div style={{ padding: 12, fontSize: 12, color: 'var(--danger)' }}>{drillDownError}</div>
      )}
    </section>
  );
};

/** Placeholder mirroring AccountPerformanceCard's shape so the grid doesn't
 *  reflow once real cards arrive after a user-switch refetch. */
const AccountCardSkeleton: FC = () => (
  <div
    style={{
      background: 'var(--bg-surface)',
      borderRadius: 'var(--radius-sm)',
      padding: 20,
      border: '1px solid transparent',
      minHeight: 200,
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
    }}
  >
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <Skeleton width={140} height={14} />
        <Skeleton width={90} height={20} />
      </div>
      <Skeleton width={120} height={32} />
    </div>
    <Skeleton height={12} />
    <Skeleton height={12} width="70%" />
    <div style={{ marginTop: 4, display: 'flex', flexDirection: 'column', gap: 6 }}>
      <Skeleton height={10} />
      <Skeleton height={10} />
      <Skeleton height={10} width="60%" />
    </div>
  </div>
);

export default LiveDeskPage;
