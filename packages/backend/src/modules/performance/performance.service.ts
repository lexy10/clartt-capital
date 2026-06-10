import { Injectable, Inject, Logger, NotFoundException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository, In, MoreThanOrEqual, Between } from 'typeorm';
import Redis from 'ioredis';
import { REDIS_CLIENT } from '../../common/modules/redis.module';
import { TimePeriod } from './performance.enums';
import {
  AggregateOverviewDto,
  AccountPerformanceDto,
  TradeDetailDto,
  InstrumentPnl,
  SparklinePoint,
  ActivityFeedItemDto,
  StrategyPerformanceDto,
} from './performance.dto';
import { Trade } from '../trades/entities/trade.entity';
import { Signal } from '../signals/entities/signal.entity';
import { TradingAccount } from '../trades/entities/trading-account.entity';
import { PortfolioSnapshot } from '../portfolios/entities/portfolio-snapshot.entity';
import { Strategy } from '../strategies/entities/strategy.entity';

@Injectable()
export class PerformanceService {
  private readonly logger = new Logger(PerformanceService.name);

  constructor(
    @InjectRepository(Trade)
    private readonly tradeRepo: Repository<Trade>,
    @InjectRepository(Signal)
    private readonly signalRepo: Repository<Signal>,
    @InjectRepository(TradingAccount)
    private readonly accountRepo: Repository<TradingAccount>,
    @InjectRepository(PortfolioSnapshot)
    private readonly snapshotRepo: Repository<PortfolioSnapshot>,
    @InjectRepository(Strategy)
    private readonly strategyRepo: Repository<Strategy>,
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
  ) {}

  /**
   * Returns the TTL in seconds for a given time period.
   * 60s for today/this_week, 300s for this_month/all_time.
   */
  getTtlForPeriod(period: TimePeriod): number {
    switch (period) {
      case TimePeriod.TODAY:
      case TimePeriod.THIS_WEEK:
        return 60;
      case TimePeriod.THIS_MONTH:
      case TimePeriod.ALL_TIME:
        return 300;
    }
  }

  /**
   * Aggregate metrics across all user accounts for a period.
   */
  async getOverview(userId: string, period: TimePeriod): Promise<AggregateOverviewDto> {
    const cacheKey = `perf:overview:${userId}:${period}`;

    // Check Redis cache
    try {
      const cached = await this.redis.get(cacheKey);
      if (cached) {
        return JSON.parse(cached);
      }
    } catch (err) {
      this.logger.warn(`Redis unavailable for cache read (${cacheKey}): ${err.message}`);
    }

    const accounts = await this.accountRepo.find({
      where: { userId, isActive: true },
    });

    if (accounts.length === 0) {
      return {
        returnMultiplier: 1.0,
        periodPercentChange: 0,
        totalBalance: 0,
        totalEquity: 0,
        todayPnl: 0,
        winRate: 0,
        profitFactor: 0,
        avgRiskReward: 0,
        maxDrawdown: 0,
        totalTrades: 0,
        accountsCount: 0,
        openPositionsCount: 0,
        totalExposure: 0,
        byBroker: [],
        topInstruments: [],
      };
    }

    const accountIds = accounts.map((a) => a.id);
    const { start, end } = this.resolveTimePeriodBounds(period);

    // Fetch closed trades within the period
    const trades = await this.fetchClosedTrades(accountIds, start, end);

    // Fetch snapshots within the period
    const snapshots = await this.fetchSnapshots(accountIds, start, end);

    // Build signal map for R:R computation
    const signalMap = await this.buildSignalMap(trades);

    // Compute aggregate metrics
    const winRate = this.computeWinRate(trades);
    const profitFactor = this.computeProfitFactor(trades);
    const avgRiskReward = this.computeAvgRiskReward(trades, signalMap);
    const maxDrawdown = this.computeMaxDrawdown(snapshots);

    // Compute weighted average return multiplier across accounts
    const accountSnapshots = new Map<string, PortfolioSnapshot[]>();
    for (const accountId of accountIds) {
      const acctSnapshots = snapshots.filter((s) => s.accountId === accountId);
      if (acctSnapshots.length > 0) {
        accountSnapshots.set(accountId, acctSnapshots);
      }
    }
    const returnMultiplier = this.computeWeightedAverageMultiplier(accountSnapshots);
    const periodPercentChange = (returnMultiplier - 1) * 100;

    // Compute dollar amounts: prefer portfolio snapshots, fall back to live
    // balance for accounts that don't snapshot (e.g. Deriv-direct accounts).
    const latestSnapshots = await this.getLatestSnapshotsPerAccount(accountIds);
    const snapshotByAccount = new Map(latestSnapshots.map((s) => [s.accountId, s]));

    let totalBalance = 0;
    let totalEquity = 0;
    // Per-broker rollups
    const brokerMap = new Map<string, {
      provider: string; accountsCount: number;
      totalEquity: number; totalBalance: number;
      periodPnl: number; openPositions: number;
      // ── For enriched fields: track per-broker trade stats ──
      winningTrades: number; losingTrades: number; totalTrades: number;
      instrumentPnl: Map<string, number>;
      accountIds: Set<string>;
    }>();

    // Pre-aggregate per-account periodPnl (sum of closed trades P&L in period)
    const pnlByAccount = new Map<string, number>();
    for (const t of trades) {
      if (!t.accountId) continue;
      const cur = pnlByAccount.get(t.accountId) ?? 0;
      pnlByAccount.set(t.accountId, cur + parseFloat(t.profitLoss as string || '0'));
    }

    for (const account of accounts) {
      const snap = snapshotByAccount.get(account.id);
      let acctEquity = 0;
      let acctBalance = 0;
      if (snap) {
        acctEquity = parseFloat(snap.equity);
        acctBalance = parseFloat(snap.balance);
      } else if (account.brokerProvider === 'deriv') {
        // No snapshot worker for Deriv — read the cached live balance
        // (kept warm every 60s by LiveBalanceRefreshService). If cache is
        // empty (cold start before the first tick fires) we leave the
        // values at 0; the periodic refresher will populate them within
        // a minute. This keeps the overview endpoint fast and predictable.
        const cached = await this.redis.get(`account:liveBalance:${account.id}`).catch(() => null);
        if (cached) {
          try {
            const parsed = JSON.parse(cached);
            acctEquity = Number(parsed.equity ?? 0);
            acctBalance = Number(parsed.balance ?? 0);
          } catch {}
        }
      }
      totalBalance += acctBalance;
      totalEquity += acctEquity;

      const provider = account.brokerProvider || 'metaapi';
      const cur = brokerMap.get(provider) ?? {
        provider, accountsCount: 0, totalEquity: 0, totalBalance: 0, periodPnl: 0, openPositions: 0,
        winningTrades: 0, losingTrades: 0, totalTrades: 0,
        instrumentPnl: new Map<string, number>(),
        accountIds: new Set<string>(),
      };
      cur.accountsCount += 1;
      cur.totalEquity += acctEquity;
      cur.totalBalance += acctBalance;
      cur.periodPnl += pnlByAccount.get(account.id) ?? 0;
      cur.accountIds.add(account.id);
      brokerMap.set(provider, cur);
    }

    // Roll per-trade stats into byBroker — win/loss counts + top instrument
    for (const t of trades) {
      if (!t.accountId) continue;
      const account = accounts.find((a) => a.id === t.accountId);
      if (!account) continue;
      const provider = account.brokerProvider || 'metaapi';
      const bucket = brokerMap.get(provider);
      if (!bucket) continue;
      const pnl = parseFloat(t.profitLoss as string || '0');
      bucket.totalTrades += 1;
      if (pnl > 0) bucket.winningTrades += 1;
      else if (pnl < 0) bucket.losingTrades += 1;
      const inst = t.instrument;
      if (inst && inst !== 'UNKNOWN') {
        bucket.instrumentPnl.set(inst, (bucket.instrumentPnl.get(inst) ?? 0) + pnl);
      }
    }

    const todayPnl = trades.reduce((sum, t) => sum + parseFloat(t.profitLoss as string || '0'), 0);

    // Count open positions (status='filled' AND closed_at IS NULL) across accounts
    const openPositionsCount = await this.tradeRepo
      .createQueryBuilder('t')
      .where('t.accountId IN (:...accountIds)', { accountIds })
      .andWhere(`t.status = 'filled'`)
      .andWhere('t.closedAt IS NULL')
      .getCount();
    const totalExposure = await this.tradeRepo
      .createQueryBuilder('t')
      .select('COALESCE(SUM(t.position_size::numeric), 0)', 'sum')
      .where('t.accountId IN (:...accountIds)', { accountIds })
      .andWhere(`t.status = 'filled'`)
      .andWhere('t.closedAt IS NULL')
      .getRawOne<{ sum: string }>()
      .then((r) => Number(r?.sum ?? 0));

    // Roll open positions back into byBroker
    if (openPositionsCount > 0) {
      const openByAcct = await this.tradeRepo
        .createQueryBuilder('t')
        .select('t.accountId', 'accountId')
        .addSelect('COUNT(*)', 'count')
        .where('t.accountId IN (:...accountIds)', { accountIds })
        .andWhere(`t.status = 'filled'`)
        .andWhere('t.closedAt IS NULL')
        .groupBy('t.accountId')
        .getRawMany<{ accountId: string; count: string }>();
      for (const row of openByAcct) {
        const acct = accounts.find((a) => a.id === row.accountId);
        if (!acct) continue;
        const provider = acct.brokerProvider || 'metaapi';
        const cur = brokerMap.get(provider);
        if (cur) cur.openPositions += Number(row.count);
      }
    }

    // Top-performing instruments — sort by total P&L over the period (not
    // trade count). Skip rows with missing/unknown instrument labels.
    const instrumentMap = new Map<string, {
      instrument: string; totalPnl: number; tradeCount: number;
      winningTrades: number; losingTrades: number;
    }>();
    for (const t of trades) {
      const inst = t.instrument;
      if (!inst || inst === 'UNKNOWN') continue;
      const cur = instrumentMap.get(inst) ?? {
        instrument: inst, totalPnl: 0, tradeCount: 0, winningTrades: 0, losingTrades: 0,
      };
      const pnl = parseFloat(t.profitLoss as string || '0');
      cur.totalPnl += pnl;
      cur.tradeCount += 1;
      if (pnl > 0) cur.winningTrades += 1;
      else if (pnl < 0) cur.losingTrades += 1;
      instrumentMap.set(inst, cur);
    }
    const topInstruments = Array.from(instrumentMap.values())
      .sort((a, b) => b.totalPnl - a.totalPnl)
      .slice(0, 5)
      .map((row) => ({
        instrument: row.instrument,
        totalPnl: row.totalPnl,
        tradeCount: row.tradeCount,
        winningTrades: row.winningTrades,
        losingTrades: row.losingTrades,
        winRate: row.tradeCount > 0
          ? (row.winningTrades / row.tradeCount) * 100
          : null,
      }));

    // Materialize broker rollups with enriched fields (win rate + top instrument).
    const byBroker = Array.from(brokerMap.values()).map((b) => {
      const decided = b.winningTrades + b.losingTrades;
      const winRate = decided > 0 ? (b.winningTrades / decided) * 100 : null;
      // Pick the highest-P&L instrument as the broker's "top"
      let topInstrument: string | null = null;
      let bestPnl = -Infinity;
      for (const [inst, pnl] of b.instrumentPnl.entries()) {
        if (pnl > bestPnl) { bestPnl = pnl; topInstrument = inst; }
      }
      return {
        provider: b.provider,
        accountsCount: b.accountsCount,
        totalEquity: b.totalEquity,
        totalBalance: b.totalBalance,
        periodPnl: b.periodPnl,
        openPositions: b.openPositions,
        winRate,
        totalTrades: b.totalTrades,
        topInstrument,
      };
    });

    const result: AggregateOverviewDto = {
      returnMultiplier,
      periodPercentChange,
      totalBalance,
      totalEquity,
      todayPnl,
      winRate,
      profitFactor,
      avgRiskReward,
      maxDrawdown,
      totalTrades: trades.length,
      accountsCount: accounts.length,
      openPositionsCount,
      totalExposure,
      byBroker,
      topInstruments,
    };

    // Store in Redis cache
    try {
      await this.redis.set(cacheKey, JSON.stringify(result), 'EX', this.getTtlForPeriod(period));
    } catch (err) {
      this.logger.warn(`Redis unavailable for cache write (${cacheKey}): ${err.message}`);
    }

    return result;
  }

  /**
   * Per-account metrics with sparkline data.
   */
  async getAccountPerformance(
    userId: string,
    period: TimePeriod,
  ): Promise<AccountPerformanceDto[]> {
    const cacheKey = `perf:accounts:${userId}:${period}`;

    // Check Redis cache
    try {
      const cached = await this.redis.get(cacheKey);
      if (cached) {
        return JSON.parse(cached);
      }
    } catch (err) {
      this.logger.warn(`Redis unavailable for cache read (${cacheKey}): ${err.message}`);
    }

    const accounts = await this.accountRepo.find({
      where: { userId, isActive: true },
    });

    if (accounts.length === 0) return [];

    const { start, end } = this.resolveTimePeriodBounds(period);
    const results: AccountPerformanceDto[] = [];

    for (const account of accounts) {
      const trades = await this.fetchClosedTrades([account.id], start, end);
      const snapshots = await this.fetchSnapshots([account.id], start, end);
      const signalMap = await this.buildSignalMap(trades);

      // Compute per-account return multiplier and period percent change
      const returnMultiplier = this.computeReturnMultiplier(snapshots);
      const periodPercentChange = (returnMultiplier - 1) * 100;

      // Get latest snapshot for balance/equity. For Deriv-direct accounts
      // there is no snapshot, so fall through to the cached liveBalance
      // (kept warm by the engine + on-demand fetch in getOverview).
      const latestSnapshot = snapshots.length > 0 ? snapshots[snapshots.length - 1] : null;
      let balance = latestSnapshot ? parseFloat(latestSnapshot.balance) : 0;
      let equity = latestSnapshot ? parseFloat(latestSnapshot.equity) : 0;
      if (!latestSnapshot && account.brokerProvider === 'deriv') {
        const cached = await this.redis
          .get(`account:liveBalance:${account.id}`)
          .catch(() => null);
        if (cached) {
          try {
            const parsed = JSON.parse(cached);
            balance = Number(parsed.balance ?? 0);
            equity = Number(parsed.equity ?? balance);
          } catch {}
        }
      }

      // Trade counts
      const winningTrades = trades.filter((t) => parseFloat(t.profitLoss as string) > 0).length;
      const losingTrades = trades.filter((t) => parseFloat(t.profitLoss as string) < 0).length;
      const periodPnl = trades.reduce(
        (sum, t) => sum + parseFloat((t.profitLoss as string) || '0'),
        0,
      );

      // Best/worst trade — prefer the actual trade row's instrument, fall
      // back to the linked signal only if missing. Skip "UNKNOWN" labels.
      let bestTrade: { pnl: number; instrument: string } | null = null;
      let worstTrade: { pnl: number; instrument: string } | null = null;

      if (trades.length > 0) {
        const tradesWithPnl = trades.map((t) => ({
          trade: t,
          pnl: parseFloat(t.profitLoss as string || '0'),
          instrument:
            t.instrument ??
            (t.signalId && signalMap.has(t.signalId)
              ? signalMap.get(t.signalId)!.instrument
              : null),
        })).filter((row) => row.instrument && row.instrument !== 'UNKNOWN');

        if (tradesWithPnl.length > 0) {
          const best = tradesWithPnl.reduce((a, b) => (b.pnl > a.pnl ? b : a));
          const worst = tradesWithPnl.reduce((a, b) => (b.pnl < a.pnl ? b : a));
          bestTrade = { pnl: best.pnl, instrument: best.instrument as string };
          worstTrade = { pnl: worst.pnl, instrument: worst.instrument as string };
        }
      }

      // Instrument breakdown (filters UNKNOWN inside the helper now too)
      const instrumentBreakdown = this.computeInstrumentBreakdown(trades, signalMap)
        .filter((row) => row.instrument && row.instrument !== 'UNKNOWN');

      // Sparkline from snapshots
      const equitySparkline: SparklinePoint[] = snapshots.map((s) => ({
        timestamp: s.snapshotAt.toISOString(),
        equity: parseFloat(s.equity),
      }));

      // Open positions count (status='filled' AND closed_at IS NULL).
      const openPositionsCount = await this.tradeRepo
        .createQueryBuilder('t')
        .where('t.accountId = :accountId', { accountId: account.id })
        .andWhere(`t.status = 'filled'`)
        .andWhere('t.closedAt IS NULL')
        .getCount();

      // Last trade time across the period
      const lastTradeAt = trades.length > 0
        ? trades.reduce((max, t) => {
            const ts = (t.closedAt ?? t.openedAt ?? t.createdAt) as Date | null;
            if (!ts) return max;
            return !max || ts > max ? ts : max;
          }, null as Date | null)
        : null;

      // Read autopilot state from Redis (engine writes "enabled"/"disabled").
      const autopilotRaw = await this.redis
        .get(`autopilot:${account.id}`)
        .catch(() => null);
      const autopilotEnabled = autopilotRaw === 'enabled'
        ? true
        : autopilotRaw === 'disabled'
          ? false
          : null;

      results.push({
        accountId: account.id,
        accountLabel: account.label || account.id,
        returnMultiplier,
        periodPercentChange,
        balance,
        equity,
        totalTrades: trades.length,
        winningTrades,
        losingTrades,
        bestTrade,
        worstTrade,
        instrumentBreakdown,
        equitySparkline,
        brokerProvider: account.brokerProvider ?? null,
        accountKind: (account as any).accountKind ?? null,
        openPositionsCount,
        periodPnl,
        autopilotEnabled,
        lastTradeAt: lastTradeAt ? lastTradeAt.toISOString() : null,
      });
    }

    // Store in Redis cache
    try {
      await this.redis.set(cacheKey, JSON.stringify(results), 'EX', this.getTtlForPeriod(period));
    } catch (err) {
      this.logger.warn(`Redis unavailable for cache write (${cacheKey}): ${err.message}`);
    }

    return results;
  }

  /**
   * Individual trade details for drill-down.
   */
  async getAccountTrades(
    userId: string,
    accountId: string,
    period: TimePeriod,
  ): Promise<TradeDetailDto[]> {
    // Verify account belongs to user
    const account = await this.accountRepo.findOne({
      where: { id: accountId, userId },
    });

    if (!account) {
      throw new NotFoundException('Trading account not found');
    }

    const cacheKey = `perf:trades:${userId}:${accountId}:${period}`;

    // Check Redis cache
    try {
      const cached = await this.redis.get(cacheKey);
      if (cached) {
        return JSON.parse(cached);
      }
    } catch (err) {
      this.logger.warn(`Redis unavailable for cache read (${cacheKey}): ${err.message}`);
    }

    const { start, end } = this.resolveTimePeriodBounds(period);
    const trades = await this.fetchClosedTrades([accountId], start, end);
    const signalMap = await this.buildSignalMap(trades);

    const result = trades.map((trade) => {
      const signal = trade.signalId ? signalMap.get(trade.signalId) : undefined;
      const instrument = signal ? signal.instrument : 'UNKNOWN';
      const pnlPips = this.computePipsForTrade(trade, instrument);
      const openedAt = trade.openedAt ? trade.openedAt.getTime() : 0;
      const closedAt = trade.closedAt ? trade.closedAt.getTime() : 0;
      const duration = (closedAt - openedAt) / 1000;

      return {
        tradeId: trade.id,
        entryTime: trade.openedAt ? trade.openedAt.toISOString() : '',
        exitTime: trade.closedAt ? trade.closedAt.toISOString() : '',
        instrument,
        direction: trade.direction,
        lotSize: parseFloat(trade.positionSize),
        entryPrice: parseFloat(trade.entryPrice as string || '0'),
        exitPrice: parseFloat(trade.exitPrice as string || '0'),
        pnlDollars: parseFloat(trade.profitLoss as string || '0'),
        pnlPips,
        duration,
      };
    });

    // Store in Redis cache
    try {
      await this.redis.set(cacheKey, JSON.stringify(result), 'EX', this.getTtlForPeriod(period));
    } catch (err) {
      this.logger.warn(`Redis unavailable for cache write (${cacheKey}): ${err.message}`);
    }

    return result;
  }

  /**
   * Invalidates all cached performance entries for a user.
   * Deletes all keys matching perf:*:{userId}:*
   */
  async invalidateUserCache(userId: string): Promise<void> {
    try {
      const patterns = [
        `perf:overview:${userId}:*`,
        `perf:accounts:${userId}:*`,
        `perf:trades:${userId}:*`,
        `perf:activity:${userId}:*`,
        `perf:strategies:${userId}:*`,
      ];

      for (const pattern of patterns) {
        const keys = await this.redis.keys(pattern);
        if (keys.length > 0) {
          await this.redis.del(...keys);
        }
      }
    } catch (err) {
      this.logger.warn(`Redis unavailable for cache invalidation (userId: ${userId}): ${err.message}`);
    }
  }

  /**
   * Returns a unified recent activity feed combining trades and signals.
   */
  async getRecentActivity(
    userId: string,
    limit = 10,
  ): Promise<ActivityFeedItemDto[]> {
    const cacheKey = `perf:activity:${userId}:${limit}`;

    try {
      const cached = await this.redis.get(cacheKey);
      if (cached) return JSON.parse(cached);
    } catch (err) {
      this.logger.warn(`Redis unavailable for cache read (${cacheKey}): ${err.message}`);
    }

    const accounts = await this.accountRepo.find({ where: { userId, isActive: true } });
    if (accounts.length === 0) return [];

    const accountIds = accounts.map((a) => a.id);
    const accountLabelMap = new Map(accounts.map((a) => [a.id, a.label || a.id]));

    // Fetch recent trades (opened + closed)
    const recentTrades = await this.tradeRepo
      .createQueryBuilder('trade')
      .where('trade.accountId IN (:...accountIds)', { accountIds })
      .orderBy('trade.createdAt', 'DESC')
      .take(limit * 2)
      .getMany();

    // Fetch recent signals
    const recentSignals = await this.signalRepo
      .createQueryBuilder('signal')
      .orderBy('signal.createdAt', 'DESC')
      .take(limit)
      .getMany();

    // Build signal map for trade instrument lookup
    const signalMap = await this.buildSignalMap(recentTrades);

    const items: ActivityFeedItemDto[] = [];

    for (const trade of recentTrades) {
      // Prefer the trade's own instrument column (always populated by the
      // execution engine on fill). Fall back to a signal join for legacy
      // trades that pre-date the column.
      const signal = trade.signalId ? signalMap.get(trade.signalId) : undefined;
      const instrument = trade.instrument ?? signal?.instrument ?? 'UNKNOWN';
      const label = trade.accountId ? accountLabelMap.get(trade.accountId) : undefined;

      if (trade.status === 'closed' && trade.closedAt) {
        const pnl = parseFloat(trade.profitLoss as string || '0');
        items.push({
          id: `trade-closed-${trade.id}`,
          type: 'trade_closed',
          instrument,
          direction: trade.direction,
          detail: `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} USD`,
          timestamp: trade.closedAt.toISOString(),
          accountLabel: label,
        });
      } else if (trade.openedAt) {
        items.push({
          id: `trade-opened-${trade.id}`,
          type: 'trade_opened',
          instrument,
          direction: trade.direction,
          detail: `${parseFloat(trade.positionSize)} lots @ ${parseFloat(trade.entryPrice as string || '0').toFixed(2)}`,
          timestamp: trade.openedAt.toISOString(),
          accountLabel: label,
        });
      }
    }

    for (const signal of recentSignals) {
      items.push({
        id: `signal-${signal.id}`,
        type: 'signal_generated',
        instrument: signal.instrument,
        direction: signal.direction,
        detail: `${signal.timeframe} — confidence ${(parseFloat(signal.confidenceScore) * 100).toFixed(0)}%`,
        timestamp: signal.createdAt.toISOString(),
      });
    }

    // Sort by timestamp descending, take limit
    items.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
    const result = items.slice(0, limit);

    try {
      await this.redis.set(cacheKey, JSON.stringify(result), 'EX', 30);
    } catch (err) {
      this.logger.warn(`Redis unavailable for cache write (${cacheKey}): ${err.message}`);
    }

    return result;
  }

  /**
   * Strategy-level performance metrics with R:R-based analytics.
   */
  async getStrategyPerformance(
    userId: string,
    period: TimePeriod,
  ): Promise<StrategyPerformanceDto[]> {
    const cacheKey = `perf:strategies:${userId}:${period}`;

    // Check Redis cache
    try {
      const cached = await this.redis.get(cacheKey);
      if (cached) {
        return JSON.parse(cached);
      }
    } catch (err) {
      this.logger.warn(`Redis unavailable for cache read (${cacheKey}): ${err.message}`);
    }

    const accounts = await this.accountRepo.find({
      where: { userId, isActive: true },
    });

    if (accounts.length === 0) return [];

    const accountIds = accounts.map((a) => a.id);
    const { start, end } = this.resolveTimePeriodBounds(period);

    // Fetch closed trades within the period
    const trades = await this.fetchClosedTrades(accountIds, start, end);

    // Build signal map from trades
    const signalMap = await this.buildSignalMap(trades);

    // Fetch all strategies to include zero-trade strategies
    const allStrategies = await this.strategyRepo.find();

    // Group trades by strategyId (via signal)
    const tradesByStrategy = new Map<string, Trade[]>();
    for (const trade of trades) {
      const signal = trade.signalId ? signalMap.get(trade.signalId) : undefined;
      if (signal?.strategyId) {
        const existing = tradesByStrategy.get(signal.strategyId) || [];
        existing.push(trade);
        tradesByStrategy.set(signal.strategyId, existing);
      }
    }

    // Build result for each strategy
    const results: StrategyPerformanceDto[] = allStrategies.map((strategy) => {
      const strategyTrades = tradesByStrategy.get(strategy.id) || [];
      const totalTrades = strategyTrades.length;

      if (totalTrades === 0) {
        return {
          strategyId: strategy.id,
          strategyName: strategy.name,
          cumulativeR: 0,
          avgR: 0,
          winRate: 0,
          totalTrades: 0,
          avgPlannedRR: 0,
          avgActualRR: 0,
          trades: [],
        };
      }

      // Compute per-trade metrics
      const tradeDetails = strategyTrades.map((trade) => {
        const signal = trade.signalId ? signalMap.get(trade.signalId) : undefined;
        const actualR = this.computeActualR(trade, signal);
        const plannedRR = this.computePlannedRR(signal);
        const actualRR = this.computeActualRR(trade, signal);

        return {
          tradeId: trade.id,
          instrument: signal?.instrument ?? 'UNKNOWN',
          direction: trade.direction,
          entryTime: trade.openedAt ? trade.openedAt.toISOString() : '',
          exitTime: trade.closedAt ? trade.closedAt.toISOString() : '',
          profitLoss: parseFloat(trade.profitLoss as string || '0'),
          actualR: actualR ?? 0,
          plannedRR: plannedRR ?? 0,
          actualRR: actualRR ?? 0,
        };
      });

      // Filter trades with valid R values for R:R aggregation
      const validRTrades = tradeDetails.filter((td) => {
        const trade = strategyTrades.find((t) => t.id === td.tradeId)!;
        const signal = trade.signalId ? signalMap.get(trade.signalId) : undefined;
        if (!signal) return false;
        const risk = Math.abs(parseFloat(signal.entryPrice) - parseFloat(signal.stopLoss));
        return risk > 0;
      });

      const cumulativeR = validRTrades.reduce((sum, td) => sum + td.actualR, 0);
      const avgR = validRTrades.length > 0 ? cumulativeR / validRTrades.length : 0;
      const avgPlannedRR = validRTrades.length > 0
        ? validRTrades.reduce((sum, td) => sum + td.plannedRR, 0) / validRTrades.length
        : 0;
      const avgActualRR = validRTrades.length > 0
        ? validRTrades.reduce((sum, td) => sum + td.actualRR, 0) / validRTrades.length
        : 0;

      // Win rate uses all trades (including those without signals)
      const winningTrades = strategyTrades.filter(
        (t) => parseFloat(t.profitLoss as string || '0') > 0,
      ).length;
      const winRate = (winningTrades / totalTrades) * 100;

      return {
        strategyId: strategy.id,
        strategyName: strategy.name,
        cumulativeR,
        avgR,
        winRate,
        totalTrades,
        avgPlannedRR,
        avgActualRR,
        trades: tradeDetails,
      };
    });

    // Cache result
    try {
      await this.redis.set(cacheKey, JSON.stringify(results), 'EX', this.getTtlForPeriod(period));
    } catch (err) {
      this.logger.warn(`Redis unavailable for cache write (${cacheKey}): ${err.message}`);
    }

    return results;
  }

  // ─── Private helper methods ───────────────────────────────────────────

  /**
   * Fetches closed trades for the given account IDs within the time period.
   */
  private async fetchClosedTrades(
    accountIds: string[],
    start: Date | null,
    end: Date,
  ): Promise<Trade[]> {
    const qb = this.tradeRepo
      .createQueryBuilder('trade')
      .where('trade.accountId IN (:...accountIds)', { accountIds })
      .andWhere('trade.status = :status', { status: 'closed' })
      .andWhere('trade.closedAt <= :end', { end });

    if (start) {
      qb.andWhere('trade.closedAt >= :start', { start });
    }

    qb.orderBy('trade.closedAt', 'ASC');

    return qb.getMany();
  }

  /**
   * Fetches portfolio snapshots for the given account IDs within the time period.
   */
  private async fetchSnapshots(
    accountIds: string[],
    start: Date | null,
    end: Date,
  ): Promise<PortfolioSnapshot[]> {
    const qb = this.snapshotRepo
      .createQueryBuilder('snapshot')
      .where('snapshot.accountId IN (:...accountIds)', { accountIds })
      .andWhere('snapshot.snapshotAt <= :end', { end });

    if (start) {
      qb.andWhere('snapshot.snapshotAt >= :start', { start });
    }

    qb.orderBy('snapshot.snapshotAt', 'ASC');

    return qb.getMany();
  }

  /**
   * Builds a map of signalId → Signal for the given trades.
   */
  private async buildSignalMap(trades: Trade[]): Promise<Map<string, Signal>> {
    const signalIds = trades
      .map((t) => t.signalId)
      .filter((id): id is string => id !== null);

    if (signalIds.length === 0) return new Map();

    const uniqueIds = [...new Set(signalIds)];
    const signals = await this.signalRepo.find({
      where: { id: In(uniqueIds) },
    });

    const map = new Map<string, Signal>();
    for (const signal of signals) {
      map.set(signal.id, signal);
    }
    return map;
  }

  /**
   * Gets the latest snapshot per account.
   */
  private async getLatestSnapshotsPerAccount(
    accountIds: string[],
  ): Promise<PortfolioSnapshot[]> {
    const results: PortfolioSnapshot[] = [];

    for (const accountId of accountIds) {
      const snapshot = await this.snapshotRepo.findOne({
        where: { accountId },
        order: { snapshotAt: 'DESC' },
      });
      if (snapshot) {
        results.push(snapshot);
      }
    }

    return results;
  }

  /**
   * Computes equity change percent from earliest to latest snapshot.
   */
  private computeEquityChangePercent(snapshots: PortfolioSnapshot[]): number {
    if (snapshots.length < 2) return 0;

    const earliest = parseFloat(snapshots[0].equity);
    const latest = parseFloat(snapshots[snapshots.length - 1].equity);

    if (earliest === 0) return 0;
    return ((latest - earliest) / earliest) * 100;
  }

  /**
   * Computes the return multiplier for a single account from its snapshots.
   * Returns current_equity / starting_equity, or 1.0 if fewer than 2 snapshots.
   */
  computeReturnMultiplier(snapshots: PortfolioSnapshot[]): number {
    if (snapshots.length < 2) return 1.0;

    const startingEquity = parseFloat(snapshots[0].equity);
    const currentEquity = parseFloat(snapshots[snapshots.length - 1].equity);

    if (startingEquity === 0) return 1.0;
    return currentEquity / startingEquity;
  }

  /**
   * Computes the weighted average return multiplier across multiple accounts.
   * Each account's multiplier is weighted by its starting equity.
   */
  computeWeightedAverageMultiplier(accountSnapshots: Map<string, PortfolioSnapshot[]>): number {
    let weightedSum = 0;
    let totalStartingEquity = 0;

    for (const [, snapshots] of accountSnapshots) {
      if (snapshots.length < 2) continue;

      const startingEquity = parseFloat(snapshots[0].equity);
      const currentEquity = parseFloat(snapshots[snapshots.length - 1].equity);

      if (startingEquity <= 0) continue;

      const multiplier = currentEquity / startingEquity;
      weightedSum += multiplier * startingEquity;
      totalStartingEquity += startingEquity;
    }

    if (totalStartingEquity === 0) return 1.0;
    return weightedSum / totalStartingEquity;
  }

  /**
   * Computes Actual R for a trade: profit_loss / (position_size * |entry_price - stop_loss|).
   * Returns null if no signal or zero risk.
   */
  computeActualR(trade: Trade, signal: Signal | undefined): number | null {
    if (!signal) return null;

    const entryPrice = parseFloat(signal.entryPrice);
    const stopLoss = parseFloat(signal.stopLoss);
    const risk = Math.abs(entryPrice - stopLoss);

    if (risk === 0) return null;

    const profitLoss = parseFloat(trade.profitLoss as string || '0');
    const positionSize = parseFloat(trade.positionSize);

    return profitLoss / (positionSize * risk);
  }

  /**
   * Computes Planned RR: |take_profit - entry_price| / |entry_price - stop_loss|.
   * Returns null if zero risk.
   */
  computePlannedRR(signal: Signal | undefined): number | null {
    if (!signal) return null;

    const entryPrice = parseFloat(signal.entryPrice);
    const stopLoss = parseFloat(signal.stopLoss);
    const takeProfit = parseFloat(signal.takeProfit);
    const risk = Math.abs(entryPrice - stopLoss);

    if (risk === 0) return null;

    return Math.abs(takeProfit - entryPrice) / risk;
  }

  /**
   * Computes Actual RR: |exit_price - entry_price| / |entry_price - stop_loss|.
   * Returns null if no signal or zero risk.
   */
  computeActualRR(trade: Trade, signal: Signal | undefined): number | null {
    if (!signal) return null;

    const entryPrice = parseFloat(signal.entryPrice);
    const stopLoss = parseFloat(signal.stopLoss);
    const risk = Math.abs(entryPrice - stopLoss);

    if (risk === 0) return null;

    const exitPrice = parseFloat(trade.exitPrice as string || '0');

    return Math.abs(exitPrice - entryPrice) / risk;
  }

  /**
   * Computes instrument breakdown from trades and signal map.
   */
  private computeInstrumentBreakdown(
    trades: Trade[],
    signalMap: Map<string, Signal>,
  ): InstrumentPnl[] {
    const breakdown = new Map<string, { totalPnl: number; tradeCount: number; wins: number; losses: number }>();

    for (const trade of trades) {
      // Prefer the trade row's own instrument column (set by the
      // engine + reconciler). Fall back to the linked signal. Skip if neither.
      const signal = trade.signalId ? signalMap.get(trade.signalId) : undefined;
      const instrument = trade.instrument ?? (signal ? signal.instrument : null);
      if (!instrument || instrument === 'UNKNOWN') continue;
      const pnl = parseFloat(trade.profitLoss as string || '0');

      const existing = breakdown.get(instrument)
        || { totalPnl: 0, tradeCount: 0, wins: 0, losses: 0 };
      existing.totalPnl += pnl;
      existing.tradeCount += 1;
      if (pnl > 0) existing.wins += 1;
      else if (pnl < 0) existing.losses += 1;
      breakdown.set(instrument, existing);
    }

    return Array.from(breakdown.entries()).map(([instrument, data]) => ({
      instrument,
      totalPnl: data.totalPnl,
      tradeCount: data.tradeCount,
      winRate: data.tradeCount > 0 ? (data.wins / data.tradeCount) * 100 : null,
      winningTrades: data.wins,
      losingTrades: data.losses,
    }));
  }

  // ─── Existing computation helpers ─────────────────────────────────────

  /**
   * Resolves the start and end date boundaries for a given time period.
   */
  resolveTimePeriodBounds(period: TimePeriod): { start: Date | null; end: Date } {
    const now = new Date();

    switch (period) {
      case TimePeriod.TODAY: {
        const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
        return { start, end: now };
      }
      case TimePeriod.THIS_WEEK: {
        const dayOfWeek = now.getUTCDay();
        const daysFromMonday = dayOfWeek === 0 ? 6 : dayOfWeek - 1;
        const start = new Date(
          Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - daysFromMonday),
        );
        return { start, end: now };
      }
      case TimePeriod.THIS_MONTH: {
        const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
        return { start, end: now };
      }
      case TimePeriod.ALL_TIME: {
        return { start: null, end: now };
      }
    }
  }

  /**
   * Computes win rate as a percentage.
   */
  computeWinRate(trades: Trade[]): number {
    if (trades.length === 0) return 0;
    const wins = trades.filter((t) => parseFloat(t.profitLoss as string) > 0).length;
    return (wins / trades.length) * 100;
  }

  /**
   * Computes profit factor = grossProfit / grossLoss.
   */
  computeProfitFactor(trades: Trade[]): number {
    let grossProfit = 0;
    let grossLoss = 0;

    for (const trade of trades) {
      const pnl = parseFloat(trade.profitLoss as string);
      if (pnl > 0) {
        grossProfit += pnl;
      } else if (pnl < 0) {
        grossLoss += Math.abs(pnl);
      }
    }

    if (grossLoss === 0) return 0;
    return grossProfit / grossLoss;
  }

  /**
   * Computes average risk:reward ratio.
   */
  computeAvgRiskReward(trades: Trade[], signals: Map<string, Signal>): number {
    if (trades.length === 0) return 0;

    let totalRR = 0;
    let count = 0;

    for (const trade of trades) {
      const signal = trade.signalId ? signals.get(trade.signalId) : undefined;
      if (!signal) {
        count++;
        continue;
      }

      const entry = parseFloat(signal.entryPrice);
      const sl = parseFloat(signal.stopLoss);
      const tp = parseFloat(signal.takeProfit);

      const risk = Math.abs(entry - sl);
      const reward = Math.abs(tp - entry);

      const rr = risk === 0 ? 0 : reward / risk;
      totalRR += rr;
      count++;
    }

    if (count === 0) return 0;
    return totalRR / count;
  }

  /**
   * Computes max drawdown as a percentage from chronological equity snapshots.
   */
  computeMaxDrawdown(snapshots: PortfolioSnapshot[]): number {
    if (snapshots.length === 0) return 0;

    let peak = parseFloat(snapshots[0].equity);
    let maxDrawdown = 0;

    for (const snapshot of snapshots) {
      const equity = parseFloat(snapshot.equity);
      if (equity > peak) {
        peak = equity;
      }
      if (peak > 0) {
        const drawdown = ((peak - equity) / peak) * 100;
        if (drawdown > maxDrawdown) {
          maxDrawdown = drawdown;
        }
      }
    }

    return maxDrawdown;
  }

  /**
   * Computes P&L in pips for a trade.
   */
  computePipsForTrade(trade: Trade, instrument: string): number {
    const pipValue = instrument === 'XAUUSD' ? 0.01 : 1.0;
    const entryPrice = parseFloat(trade.entryPrice as string);
    const exitPrice = parseFloat(trade.exitPrice as string);

    if (trade.direction === 'BUY') {
      return (exitPrice - entryPrice) / pipValue;
    } else {
      return (entryPrice - exitPrice) / pipValue;
    }
  }

  /**
   * Fetch live Deriv account balance + open positions count from the
   * execution engine. Used as a fallback when the Redis liveBalance cache
   * is empty (e.g., right after engine restart). The engine's endpoint
   * authorizes with the per-account token and returns balance/equity.
   */
  private async fetchDerivBalanceLive(
    account: TradingAccount,
  ): Promise<{ balance: number; equity: number; open_positions: number } | null> {
    const engineUrl =
      process.env.EXECUTION_ENGINE_URL || 'http://execution-engine:8002';
    try {
      const resp = await fetch(`${engineUrl}/accounts/deriv/details`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          login_id: account.derivLoginId,
          api_token: account.derivApiToken,
        }),
        // Node fetch supports AbortSignal.timeout (Node 18+)
        signal: AbortSignal.timeout(8000),
      });
      if (!resp.ok) {
        this.logger.warn(
          `Deriv details endpoint returned ${resp.status} for ${account.id}`,
        );
        return null;
      }
      const data = (await resp.json()) as {
        balance?: number; equity?: number; open_positions?: number;
      };
      return {
        balance: Number(data?.balance ?? 0),
        equity: Number(data?.equity ?? data?.balance ?? 0),
        open_positions: Number(data?.open_positions ?? 0),
      };
    } catch (err) {
      this.logger.warn(
        `Deriv details fetch threw for ${account.id}: ${(err as Error).message}`,
      );
      return null;
    }
  }
}
