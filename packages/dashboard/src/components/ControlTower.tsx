import { type FC, useEffect, useRef } from 'react';
import { usePerformanceStore } from '../stores/performanceStore';
import { useHealthStore } from '../stores/healthStore';
import { useStrategyStore } from '../stores/strategyStore';
import { useChartStore } from '../stores/chartStore';
import { useInstrumentStore } from '../stores/instrumentStore';
import { useSignalStore } from '../stores/signalStore';
import { usePortfolioStore } from '../stores/portfolioStore';
import { useEventStore } from '../stores/eventStore';
import { useReconciliationStore } from '../stores/reconciliationStore';
import { useAgentStore } from '../stores/agentStore';
// Operational widgets that live in the right rail on trading contexts.
import KillSwitchPanel from './KillSwitchPanel';
import AutopilotStatusCard from './AutopilotStatusCard';
import HealthSummaryCard from './operations/HealthSummaryCard';
import ReconciliationSummaryCard from './operations/ReconciliationSummaryCard';
import Skeleton from './Skeleton';

type PanelContext =
  | 'live-desk'
  | 'analytics'
  | 'chart'
  | 'strategies'
  | 'accounts'
  | 'signals'
  | 'positions'
  | 'events'
  | 'reconciliation'
  | 'admin'
  | 'agents'
  | 'system'
  | 'default';

interface ControlTowerProps {
  context?: PanelContext;
}

const fmt = (v: number) =>
  `${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const pnlColor = (v: number) =>
  v > 0 ? 'var(--success)' : v < 0 ? 'var(--danger)' : 'var(--text-secondary)';

const ControlTower: FC<ControlTowerProps> = ({ context = 'default' }) => {
  const overview = usePerformanceStore((s) => s.overview);
  const overviewLoading = usePerformanceStore((s) => s.overviewLoading);
  const accounts = usePerformanceStore((s) => s.accounts);
  const accountsLoading = usePerformanceStore((s) => s.accountsLoading);

  // Track first-ever-load so skeletons only appear on the initial fetch,
  // not on every background refresh.
  const hasSeenOverviewRef = useRef(overview !== null);

  // Track whether a fetch has actually completed at least once. Before that
  // — on the very first render before useEffect fires the initial fetch —
  // the store's empty defaults shouldn't make us flash the "Get Started"
  // empty-state card.
  const hasFetchedAccountsOnceRef = useRef(false);
  if (accountsLoading || accounts.length > 0) {
    hasFetchedAccountsOnceRef.current = true;
  }
  const fetchOverview = usePerformanceStore((s) => s.fetchOverview);
  const fetchAccounts = usePerformanceStore((s) => s.fetchAccounts);

  // Context-specific stores
  const strategies = useStrategyStore((s) => s.strategies);
  const fetchStrategies = useStrategyStore((s) => s.fetchStrategies);
  const backtestsByStrategy = useStrategyStore((s) => s.backtestsByStrategy);

  const services = useHealthStore((s) => s.services);
  const circuitBreakers = useHealthStore((s) => s.circuitBreakers);
  const consumerLags = useHealthStore((s) => s.consumerLags);
  const fetchHealthSnapshot = useHealthStore((s) => s.fetchHealthSnapshot);

  const selectedInstrument = useChartStore((s) => s.instrument);
  const instruments = useInstrumentStore((s) => s.instruments);
  const fetchInstruments = useInstrumentStore((s) => s.fetchInstruments);

  // Page-specific stores
  const signals = useSignalStore((s) => s.signals);
  const fetchSignals = useSignalStore((s) => s.fetchSignals);

  const positions = usePortfolioStore((s) => s.positions);
  const fetchPositions = usePortfolioStore((s) => s.fetchPositions);

  const events = useEventStore((s) => s.events);
  const totalEventCount = useEventStore((s) => s.totalCount);
  const fetchEvents = useEventStore((s) => s.fetchEvents);

  const reconciliationReports = useReconciliationStore((s) => s.reports);
  const fetchReconciliationReports = useReconciliationStore((s) => s.fetchReports);

  const agents = useAgentStore((s) => s.agents);
  const pendingApprovals = useAgentStore((s) => s.pendingApprovals);
  const queueDepth = useAgentStore((s) => s.queueDepth);
  const fetchAgents = useAgentStore((s) => s.fetchAgents);

  useEffect(() => {
    // Always fetch core data on mount.
    fetchOverview();
    fetchAccounts();
    // NOTE: we DON'T subscribe to 'account_sync' here. LayoutShell already
    // wires usePerformanceStore.subscribeToSync(), which fires the exact
    // same fetchOverview + fetchAccounts. Subscribing again here doubles
    // every refresh — race conditions between two concurrent fetches
    // (both flipping accountsLoading) can briefly flicker the cards.
  }, [fetchOverview, fetchAccounts]);

  // Fetch context-specific data
  useEffect(() => {
    if (context === 'strategies') {
      fetchStrategies();
    }
    if (context === 'system') {
      fetchHealthSnapshot();
    }
    if (context === 'chart') {
      fetchInstruments();
    }
    if (context === 'signals') {
      fetchSignals();
    }
    if (context === 'positions') {
      fetchPositions();
    }
    if (context === 'events') {
      fetchEvents();
    }
    if (context === 'reconciliation') {
      fetchReconciliationReports();
    }
    if (context === 'admin') {
      fetchInstruments();
    }
    if (context === 'agents') {
      fetchAgents();
    }
  }, [
    context,
    fetchStrategies,
    fetchHealthSnapshot,
    fetchInstruments,
    fetchSignals,
    fetchPositions,
    fetchEvents,
    fetchReconciliationReports,
    fetchAgents,
  ]);

  const totalBalance = overview?.totalBalance ?? 0;
  const totalEquity = overview?.totalEquity ?? 0;
  const todayPnl = overview?.todayPnl ?? 0;

  // The right rail shows operational widgets on trading-active contexts.
  // Order (top → bottom): Kill Switch · Portfolio · Autopilot · System · Reconciliation.
  // Accounts list + Recent Activity used to live here too; they moved to the
  // main page body where the multi-account summary + activity feed render.
  const showOperations = context === 'live-desk' || context === 'positions' || context === 'default';

  return (
    <>
      {/* ── Kill Switch (top of rail — most critical control) ── */}
      {showOperations && <KillSwitchPanel />}

      {/* ── Portfolio Summary (always shown) ──
       *  Once we've seen an overview, never blink back to skeleton during a
       *  refresh — keep the last good numbers visible. */}
      {(() => {
        const hasOverview = overview !== null;
        // Track first-ever-load so skeletons appear only initially.
        if (hasOverview) hasSeenOverviewRef.current = true;
        const showSkeleton = !hasOverview && !hasSeenOverviewRef.current && overviewLoading;
        return (
          <div className="card">
            <div className="card-header">
              <span className="card-title">Portfolio</span>
            </div>
            <div className="stats-grid">
              <div className="stat-item">
                <span className="stat-label">Total Balance</span>
                <span className="stat-value">
                  {showSkeleton ? <Skeleton width={64} height={16} /> : fmt(totalBalance)}
                </span>
              </div>
              <div className="stat-item">
                <span className="stat-label">Total Equity</span>
                <span className="stat-value">
                  {showSkeleton ? <Skeleton width={64} height={16} /> : fmt(totalEquity)}
                </span>
              </div>
              <div className="stat-item" style={{ gridColumn: '1 / -1' }}>
                <span className="stat-label">Today's P&L</span>
                <span className="stat-value" style={{ color: pnlColor(todayPnl) }}>
                  {showSkeleton ? <Skeleton width={80} height={16} /> : `${todayPnl >= 0 ? '+' : ''}${fmt(todayPnl)}`}
                </span>
              </div>
            </div>
          </div>
        );
      })()}

      {/* ── Operations stack (Autopilot · System · Reconciliation) ── */}
      {showOperations && (
        <>
          <AutopilotStatusCard />
          <HealthSummaryCard />
          <ReconciliationSummaryCard />
        </>
      )}

      {/* ── Get Started (only when a fetch has completed AND confirmed empty) ──
       *  Three gates needed: (a) accounts is empty, (b) we're not currently
       *  fetching, (c) we've finished at least one fetch — otherwise the very
       *  first render (before useEffect kicks the fetch) would flash this
       *  card for one frame against the store's empty defaults. */}
      {accounts.length === 0 && !accountsLoading && hasFetchedAccountsOnceRef.current && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Get Started</span>
          </div>
          <div style={{ padding: '12px', fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            <p style={{ margin: '0 0 8px' }}>Set up your first trading account:</p>
            <ol style={{ margin: 0, paddingLeft: 16 }}>
              <li>Go to <strong>Accounts</strong> → Add Account</li>
              <li>Deploy the account</li>
              <li>Map instruments (R_75, R_25)</li>
              <li>Assign strategies</li>
              <li>Enable autopilot</li>
            </ol>
          </div>
        </div>
      )}

      {/* ── Context: live-desk — Quick Status ── */}
      {context === 'live-desk' && (
        <QuickStatusCard overview={overview} strategies={strategies} />
      )}

      {/* ── Context: analytics — Performance Snapshot ── */}
      {context === 'analytics' && (
        <PerformanceSnapshotCard overview={overview} />
      )}

      {/* ── Context: chart — Instrument Info + Watchlist ── */}
      {context === 'chart' && (
        <>
          <InstrumentInfoCard instrument={selectedInstrument} instruments={instruments} />
          <WatchlistMiniCard />
        </>
      )}

      {/* ── Context: strategies — Strategy Overview + Recent Backtests ── */}
      {context === 'strategies' && (
        <>
          <StrategyOverviewCard strategies={strategies} backtestsByStrategy={backtestsByStrategy} />
          <RecentBacktestsCard strategies={strategies} backtestsByStrategy={backtestsByStrategy} />
        </>
      )}

      {/* ── Context: system — System Status ── */}
      {context === 'system' && (
        <SystemStatusCard services={services} circuitBreakers={circuitBreakers} consumerLags={consumerLags} />
      )}

      {/* ── Context: signals — Signal Stats + Recent ── */}
      {context === 'signals' && (
        <SignalStatsCard signals={signals} />
      )}

      {/* ── Context: positions — Open Positions Summary ── */}
      {context === 'positions' && (
        <PositionsSummaryCard positions={positions} />
      )}

      {/* ── Context: events — Event Stream Summary ── */}
      {context === 'events' && (
        <EventStreamCard events={events} totalCount={totalEventCount} />
      )}

      {/* ── Context: reconciliation — Reconciliation Status ── */}
      {context === 'reconciliation' && (
        <ReconciliationStatusCard reports={reconciliationReports} />
      )}

      {/* ── Context: admin — Admin Quick Stats ── */}
      {context === 'admin' && (
        <AdminQuickStatsCard instruments={instruments} strategies={strategies} />
      )}

      {/* ── Context: agents — Agent Quick Status ── */}
      {context === 'agents' && (
        <AgentQuickStatusCard agents={agents} pendingApprovals={pendingApprovals} queueDepth={queueDepth} />
      )}

      {/* ── Accounts list + Recent Activity moved to main page body ── */}
    </>
  );
};

export default ControlTower;

/* ─────────────────────────────────────────────
 * Context-specific sub-components
 * ───────────────────────────────────────────── */

/** live-desk: Quick Status card */
const QuickStatusCard: FC<{
  overview: ReturnType<typeof usePerformanceStore.getState>['overview'];
  strategies: ReturnType<typeof useStrategyStore.getState>['strategies'];
}> = ({ overview, strategies }) => {
  const enabledCount = strategies.filter((s) => s.enabled).length;

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Quick Status</span>
      </div>
      <div className="stats-grid">
        <div className="stat-item">
          <span className="stat-label">Active Strategies</span>
          <span className="stat-value">{enabledCount}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Total Trades</span>
          <span className="stat-value">{overview?.totalTrades ?? 0}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Win Rate</span>
          <span className="stat-value">{(Number(overview?.winRate ?? 0) * 100).toFixed(1)}%</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Max Drawdown</span>
          <span className="stat-value" style={{ color: 'var(--danger)' }}>
            {((overview?.maxDrawdown ?? 0) * 100).toFixed(1)}%
          </span>
        </div>
      </div>
    </div>
  );
};

/** analytics: Performance Snapshot card */
const PerformanceSnapshotCard: FC<{
  overview: ReturnType<typeof usePerformanceStore.getState>['overview'];
}> = ({ overview }) => {
  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Performance Snapshot</span>
      </div>
      <div className="stats-grid">
        <div className="stat-item">
          <span className="stat-label">Win Rate</span>
          <span className="stat-value">{(Number(overview?.winRate ?? 0) * 100).toFixed(1)}%</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Profit Factor</span>
          <span className="stat-value">{Number(overview?.profitFactor ?? 0).toFixed(2)}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Total Trades</span>
          <span className="stat-value">{overview?.totalTrades ?? 0}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Max Drawdown</span>
          <span className="stat-value" style={{ color: 'var(--danger)' }}>
            {((overview?.maxDrawdown ?? 0) * 100).toFixed(1)}%
          </span>
        </div>
      </div>
    </div>
  );
};

/** chart: Instrument Info card */
const InstrumentInfoCard: FC<{
  instrument: string;
  instruments: ReturnType<typeof useInstrumentStore.getState>['instruments'];
}> = ({ instrument, instruments }) => {
  const info = instruments.find((i) => i.symbol === instrument);

  if (!instrument) {
    return (
      <div className="card">
        <div className="card-header">
          <span className="card-title">Instrument Info</span>
        </div>
        <div style={{ padding: '12px', fontSize: 11, color: 'var(--text-muted)', textAlign: 'center' }}>
          Select an instrument to view specs
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">{info?.displayName ?? instrument}</span>
      </div>
      <div className="stats-grid">
        <div className="stat-item">
          <span className="stat-label">Pip Size</span>
          <span className="stat-value">{info?.pipSize ?? '—'}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Contract Size</span>
          <span className="stat-value">{info?.contractSize ?? '—'}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Min Lot</span>
          <span className="stat-value">{info?.minLot ?? '—'}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Leverage</span>
          <span className="stat-value">{info?.leverage ? `1:${info.leverage}` : '—'}</span>
        </div>
      </div>
    </div>
  );
};

/** chart: Watchlist mini-card */
const WatchlistMiniCard: FC = () => {
  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Watchlist</span>
      </div>
      <div style={{ padding: '12px', fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
        <p style={{ margin: 0 }}>
          Manage your watchlists and tracked instruments from the{' '}
          <strong>Chart</strong> page instrument tabs.
        </p>
      </div>
    </div>
  );
};

/** strategies: Strategy Overview card */
const StrategyOverviewCard: FC<{
  strategies: ReturnType<typeof useStrategyStore.getState>['strategies'];
  backtestsByStrategy: ReturnType<typeof useStrategyStore.getState>['backtestsByStrategy'];
}> = ({ strategies, backtestsByStrategy }) => {
  const enabledCount = strategies.filter((s) => s.enabled).length;

  // Find the most recent backtest across all strategies
  let lastBacktestDate: string | null = null;
  for (const results of Object.values(backtestsByStrategy)) {
    for (const bt of results) {
      const date = bt.created_at ?? bt.createdAt;
      if (date && (!lastBacktestDate || date > lastBacktestDate)) {
        lastBacktestDate = date;
      }
    }
  }

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Strategy Overview</span>
      </div>
      <div className="stats-grid">
        <div className="stat-item">
          <span className="stat-label">Total Strategies</span>
          <span className="stat-value">{strategies.length}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Enabled</span>
          <span className="stat-value" style={{ color: enabledCount > 0 ? 'var(--success)' : 'var(--text-muted)' }}>
            {enabledCount}
          </span>
        </div>
        <div className="stat-item" style={{ gridColumn: '1 / -1' }}>
          <span className="stat-label">Last Backtest</span>
          <span className="stat-value" style={{ fontSize: 11 }}>
            {lastBacktestDate
              ? new Date(lastBacktestDate).toLocaleDateString()
              : 'None'}
          </span>
        </div>
      </div>
    </div>
  );
};

/** strategies: Recent Backtests mini-list */
const RecentBacktestsCard: FC<{
  strategies: ReturnType<typeof useStrategyStore.getState>['strategies'];
  backtestsByStrategy: ReturnType<typeof useStrategyStore.getState>['backtestsByStrategy'];
}> = ({ strategies, backtestsByStrategy }) => {
  // Collect all backtests, sort by date, take last 3
  const allBacktests: Array<{ strategyName: string; winRate: number | null; profitFactor: number | null; date: string }> = [];
  for (const [strategyId, results] of Object.entries(backtestsByStrategy)) {
    const strategy = strategies.find((s) => s.id === strategyId);
    for (const bt of results) {
      if (bt.status === 'completed') {
        allBacktests.push({
          strategyName: strategy?.name ?? 'Unknown',
          winRate: bt.win_rate ?? bt.winRate ?? null,
          profitFactor: bt.profit_factor ?? bt.profitFactor ?? null,
          date: bt.created_at ?? bt.createdAt ?? '',
        });
      }
    }
  }
  allBacktests.sort((a, b) => (b.date > a.date ? 1 : -1));
  const recent = allBacktests.slice(0, 3);

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Recent Backtests</span>
      </div>
      {recent.length > 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {recent.map((bt, i) => (
            <div
              key={i}
              style={{
                padding: '8px 12px',
                background: 'rgba(255,255,255,0.02)',
                borderRadius: 'var(--radius-md)',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 2 }}>
                  {bt.strategyName}
                </div>
                <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                  {bt.date ? new Date(bt.date).toLocaleDateString() : '—'}
                </div>
              </div>
              <div style={{ textAlign: 'right', fontSize: 10, fontFamily: 'var(--font-mono)' }}>
                <div style={{ color: 'var(--text-secondary)' }}>
                  WR {bt.winRate != null ? `${(Number(bt.winRate) * 100).toFixed(0)}%` : '—'}
                </div>
                <div style={{ color: 'var(--text-muted)' }}>
                  PF {bt.profitFactor != null ? Number(bt.profitFactor).toFixed(2) : '—'}
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ padding: '12px', fontSize: 11, color: 'var(--text-muted)', textAlign: 'center' }}>
          No completed backtests yet
        </div>
      )}
    </div>
  );
};

/** system: System Status card */
const SystemStatusCard: FC<{
  services: ReturnType<typeof useHealthStore.getState>['services'];
  circuitBreakers: ReturnType<typeof useHealthStore.getState>['circuitBreakers'];
  consumerLags: ReturnType<typeof useHealthStore.getState>['consumerLags'];
}> = ({ services, circuitBreakers, consumerLags }) => {
  const healthyCount = services.filter((s) => s.status === 'healthy').length;
  const totalServices = services.length;
  const openBreakers = circuitBreakers.filter((cb) => cb.state === 'open').length;
  const lagAlerts = consumerLags.filter((l) => l.lag > l.threshold).length;

  const statusColor = healthyCount === totalServices
    ? 'var(--success)'
    : healthyCount > 0
      ? 'var(--warning, #f59e0b)'
      : 'var(--danger)';

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">System Status</span>
      </div>
      <div className="stats-grid">
        <div className="stat-item">
          <span className="stat-label">Services</span>
          <span className="stat-value" style={{ color: statusColor }}>
            {healthyCount}/{totalServices}
          </span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Open Breakers</span>
          <span className="stat-value" style={{ color: openBreakers > 0 ? 'var(--danger)' : 'var(--success)' }}>
            {openBreakers}
          </span>
        </div>
        <div className="stat-item" style={{ gridColumn: '1 / -1' }}>
          <span className="stat-label">Consumer Lag Alerts</span>
          <span className="stat-value" style={{ color: lagAlerts > 0 ? 'var(--warning, #f59e0b)' : 'var(--text-secondary)' }}>
            {lagAlerts}
          </span>
        </div>
      </div>
      {/* Per-service breakdown */}
      {services.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 8 }}>
          {services.map((svc) => (
            <div
              key={svc.service}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '4px 12px',
                fontSize: 11,
              }}
            >
              <span style={{ color: 'var(--text-secondary)' }}>{svc.service}</span>
              <span
                style={{
                  color:
                    svc.status === 'healthy'
                      ? 'var(--success)'
                      : svc.status === 'degraded'
                        ? 'var(--warning, #f59e0b)'
                        : 'var(--danger)',
                  fontWeight: 600,
                  fontSize: 10,
                  textTransform: 'uppercase',
                }}
              >
                {svc.status}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

/** signals: Signal Stats card with breakdown */
const SignalStatsCard: FC<{
  signals: ReturnType<typeof useSignalStore.getState>['signals'];
}> = ({ signals }) => {
  const total = signals.length;
  const buys = signals.filter((s) => s.direction === 'BUY').length;
  const sells = signals.filter((s) => s.direction === 'SELL').length;

  // Most recent signals (top 3)
  const recent = [...signals]
    .sort((a, b) => (b.created_at > a.created_at ? 1 : -1))
    .slice(0, 3);

  return (
    <>
      <div className="card">
        <div className="card-header">
          <span className="card-title">Signal Stats</span>
        </div>
        <div className="stats-grid">
          <div className="stat-item">
            <span className="stat-label">Total</span>
            <span className="stat-value">{total}</span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Buys</span>
            <span className="stat-value" style={{ color: 'var(--success)' }}>{buys}</span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Sells</span>
            <span className="stat-value" style={{ color: 'var(--danger)' }}>{sells}</span>
          </div>
          <div className="stat-item">
            <span className="stat-label">B/S Ratio</span>
            <span className="stat-value">
              {sells > 0 ? (buys / sells).toFixed(2) : buys > 0 ? '∞' : '—'}
            </span>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <span className="card-title">Latest Signals</span>
        </div>
        {recent.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {recent.map((sig) => (
              <div
                key={sig.id}
                style={{
                  padding: '8px 12px',
                  background: 'rgba(255,255,255,0.02)',
                  borderRadius: 'var(--radius-md)',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-primary)' }}>
                    {sig.instrument}
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                    {new Date(sig.created_at).toLocaleTimeString()}
                  </div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div
                    style={{
                      fontSize: 10,
                      fontWeight: 700,
                      color: sig.direction === 'BUY' ? 'var(--success)' : 'var(--danger)',
                    }}
                  >
                    {sig.direction}
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                    {sig.entry_price.toFixed(2)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ padding: '12px', fontSize: 11, color: 'var(--text-muted)', textAlign: 'center' }}>
            No signals yet
          </div>
        )}
      </div>
    </>
  );
};

/** positions: Open Positions Summary card */
const PositionsSummaryCard: FC<{
  positions: ReturnType<typeof usePortfolioStore.getState>['positions'];
}> = ({ positions }) => {
  const totalUnrealized = positions.reduce(
    (acc, p) => acc + (p.unrealized_pnl ?? 0),
    0,
  );
  const longCount = positions.filter((p) => p.direction === 'BUY').length;
  const shortCount = positions.filter((p) => p.direction === 'SELL').length;
  const winning = positions.filter((p) => (p.unrealized_pnl ?? 0) > 0).length;
  const losing = positions.filter((p) => (p.unrealized_pnl ?? 0) < 0).length;

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Open Positions</span>
      </div>
      <div className="stats-grid">
        <div className="stat-item">
          <span className="stat-label">Total Open</span>
          <span className="stat-value">{positions.length}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Unrealized P&L</span>
          <span className="stat-value" style={{ color: pnlColor(totalUnrealized) }}>
            {totalUnrealized >= 0 ? '+' : ''}{fmt(totalUnrealized)}
          </span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Long</span>
          <span className="stat-value" style={{ color: 'var(--success)' }}>{longCount}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Short</span>
          <span className="stat-value" style={{ color: 'var(--danger)' }}>{shortCount}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Winning</span>
          <span className="stat-value" style={{ color: 'var(--success)' }}>{winning}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Losing</span>
          <span className="stat-value" style={{ color: 'var(--danger)' }}>{losing}</span>
        </div>
      </div>
    </div>
  );
};

/** events: Event Stream Summary card */
const EventStreamCard: FC<{
  events: ReturnType<typeof useEventStore.getState>['events'];
  totalCount: number;
}> = ({ events, totalCount }) => {
  // Count events by type
  const typeCounts: Record<string, number> = {};
  for (const ev of events) {
    typeCounts[ev.eventType] = (typeCounts[ev.eventType] ?? 0) + 1;
  }
  // Top 4 event types in current view
  const topTypes = Object.entries(typeCounts)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 4);

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Event Stream</span>
      </div>
      <div className="stats-grid">
        <div className="stat-item" style={{ gridColumn: '1 / -1' }}>
          <span className="stat-label">Total Events</span>
          <span className="stat-value">{totalCount.toLocaleString()}</span>
        </div>
      </div>
      {topTypes.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 8 }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', padding: '0 12px', textTransform: 'uppercase', letterSpacing: 0.5 }}>
            Recent Distribution
          </div>
          {topTypes.map(([type, count]) => (
            <div
              key={type}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '4px 12px',
                fontSize: 11,
              }}
            >
              <span style={{ color: 'var(--text-secondary)' }}>{type}</span>
              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>
                {count}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

/** reconciliation: Status card */
const ReconciliationStatusCard: FC<{
  reports: ReturnType<typeof useReconciliationStore.getState>['reports'];
}> = ({ reports }) => {
  const recent = reports.slice(0, 5);
  const successCount = reports.filter((r) => r.status === 'success').length;
  const failedCount = reports.filter((r) => r.status === 'failed').length;
  const totalDiscrepancies = reports.reduce(
    (acc, r) => acc + (r.discrepancies?.length ?? 0),
    0,
  );

  return (
    <>
      <div className="card">
        <div className="card-header">
          <span className="card-title">Reconciliation</span>
        </div>
        <div className="stats-grid">
          <div className="stat-item">
            <span className="stat-label">Total Cycles</span>
            <span className="stat-value">{reports.length}</span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Success</span>
            <span className="stat-value" style={{ color: 'var(--success)' }}>{successCount}</span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Failed</span>
            <span className="stat-value" style={{ color: failedCount > 0 ? 'var(--danger)' : 'var(--text-secondary)' }}>
              {failedCount}
            </span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Discrepancies</span>
            <span className="stat-value" style={{ color: totalDiscrepancies > 0 ? 'var(--warning, #f59e0b)' : 'var(--text-secondary)' }}>
              {totalDiscrepancies}
            </span>
          </div>
        </div>
      </div>

      {recent.length > 0 && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Recent Cycles</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {recent.map((r) => (
              <div
                key={r.id}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: '6px 12px',
                  fontSize: 11,
                  background: 'rgba(255,255,255,0.02)',
                  borderRadius: 'var(--radius-md)',
                }}
              >
                <span style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
                  {new Date(r.cycleTimestamp).toLocaleTimeString()}
                </span>
                <span
                  style={{
                    color:
                      r.status === 'success'
                        ? 'var(--success)'
                        : r.status === 'failed'
                          ? 'var(--danger)'
                          : 'var(--warning, #f59e0b)',
                    fontWeight: 600,
                    fontSize: 10,
                    textTransform: 'uppercase',
                  }}
                >
                  {r.status}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
};

/** admin: Quick Stats card for admin pages */
const AdminQuickStatsCard: FC<{
  instruments: ReturnType<typeof useInstrumentStore.getState>['instruments'];
  strategies: ReturnType<typeof useStrategyStore.getState>['strategies'];
}> = ({ instruments, strategies }) => {
  const activeInstruments = instruments.filter((i) => i.isActive).length;
  const enabledStrategies = strategies.filter((s) => s.enabled).length;
  const indices = instruments.filter((i) => i.type === 'index').length;
  const synthetics = instruments.filter((i) => i.type === 'synthetic').length;
  const commodities = instruments.filter((i) => i.type === 'commodity').length;

  return (
    <>
      <div className="card">
        <div className="card-header">
          <span className="card-title">Admin Overview</span>
        </div>
        <div className="stats-grid">
          <div className="stat-item">
            <span className="stat-label">Instruments</span>
            <span className="stat-value">{instruments.length}</span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Active</span>
            <span className="stat-value" style={{ color: 'var(--success)' }}>{activeInstruments}</span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Strategies</span>
            <span className="stat-value">{strategies.length}</span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Enabled</span>
            <span className="stat-value" style={{ color: enabledStrategies > 0 ? 'var(--success)' : 'var(--text-muted)' }}>
              {enabledStrategies}
            </span>
          </div>
        </div>
      </div>

      {instruments.length > 0 && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">By Type</span>
          </div>
          <div className="stats-grid">
            <div className="stat-item">
              <span className="stat-label">Indices</span>
              <span className="stat-value">{indices}</span>
            </div>
            <div className="stat-item">
              <span className="stat-label">Synthetic</span>
              <span className="stat-value">{synthetics}</span>
            </div>
            <div className="stat-item" style={{ gridColumn: '1 / -1' }}>
              <span className="stat-label">Commodities</span>
              <span className="stat-value">{commodities}</span>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

/** agents: Quick Status card */
const AgentQuickStatusCard: FC<{
  agents: ReturnType<typeof useAgentStore.getState>['agents'];
  pendingApprovals: ReturnType<typeof useAgentStore.getState>['pendingApprovals'];
  queueDepth: ReturnType<typeof useAgentStore.getState>['queueDepth'];
}> = ({ agents, pendingApprovals, queueDepth }) => {
  const activeAgents = agents.filter((a) => a.enabled && a.state !== 'IDLE' && a.state !== 'COMPLETED').length;
  const errorAgents = agents.filter((a) => a.state === 'FAILED').length;
  const totalQueue = (queueDepth.CRITICAL ?? 0) + (queueDepth.HIGH ?? 0) + (queueDepth.NORMAL ?? 0) + (queueDepth.LOW ?? 0);
  const totalCost = agents.reduce((acc, a) => acc + (a.llm_cost_today_usd ?? 0), 0);

  return (
    <>
      <div className="card">
        <div className="card-header">
          <span className="card-title">Agents</span>
        </div>
        <div className="stats-grid">
          <div className="stat-item">
            <span className="stat-label">Total</span>
            <span className="stat-value">{agents.length}</span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Active</span>
            <span className="stat-value" style={{ color: activeAgents > 0 ? 'var(--success)' : 'var(--text-muted)' }}>
              {activeAgents}
            </span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Errors</span>
            <span className="stat-value" style={{ color: errorAgents > 0 ? 'var(--danger)' : 'var(--text-secondary)' }}>
              {errorAgents}
            </span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Queue</span>
            <span className="stat-value">{totalQueue}</span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Pending Approvals</span>
            <span className="stat-value" style={{ color: pendingApprovals.length > 0 ? 'var(--warning, #f59e0b)' : 'var(--text-secondary)' }}>
              {pendingApprovals.length}
            </span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Today's Cost</span>
            <span className="stat-value" style={{ fontFamily: 'var(--font-mono)' }}>
              ${totalCost.toFixed(2)}
            </span>
          </div>
        </div>
      </div>

      {agents.length > 0 && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Status</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {agents.map((a) => (
              <div
                key={a.name}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: '4px 12px',
                  fontSize: 11,
                }}
              >
                <span style={{ color: 'var(--text-secondary)' }}>{a.name}</span>
                <span
                  style={{
                    color:
                      a.state === 'FAILED'
                        ? 'var(--danger)'
                        : a.state === 'EXECUTING' || a.state === 'PLANNING'
                          ? 'var(--success)'
                          : a.state === 'PAUSED' || a.state === 'WAITING_FOR_INPUT'
                            ? 'var(--warning, #f59e0b)'
                            : 'var(--text-muted)',
                    fontWeight: 600,
                    fontSize: 10,
                    textTransform: 'uppercase',
                  }}
                >
                  {a.state}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
};
