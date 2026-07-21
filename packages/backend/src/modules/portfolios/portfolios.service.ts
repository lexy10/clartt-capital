import { Injectable, Inject, Logger } from '@nestjs/common';
import { HttpService } from '@nestjs/axios';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository, In, IsNull } from 'typeorm';
import { Interval } from '@nestjs/schedule';
import Redis from 'ioredis';
import { firstValueFrom } from 'rxjs';
import { REDIS_CLIENT } from '../../common/modules/redis.module';
import { TradingGateway } from '../gateway/trading.gateway';
import { PerformanceService } from '../performance/performance.service';
import { Position } from '../trades/entities/position.entity';
import { Trade } from '../trades/entities/trade.entity';
import { TradingAccount } from '../trades/entities/trading-account.entity';
import { PortfolioSnapshot } from './entities/portfolio-snapshot.entity';

@Injectable()
export class PortfoliosService {
  private readonly logger = new Logger(PortfoliosService.name);

  constructor(
    @InjectRepository(Position)
    private readonly positionsRepository: Repository<Position>,
    @InjectRepository(Trade)
    private readonly tradesRepository: Repository<Trade>,
    @InjectRepository(TradingAccount)
    private readonly tradingAccountsRepository: Repository<TradingAccount>,
    @InjectRepository(PortfolioSnapshot)
    private readonly snapshotsRepository: Repository<PortfolioSnapshot>,
    private readonly httpService: HttpService,
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
    private readonly tradingGateway: TradingGateway,
    private readonly performanceService: PerformanceService,
  ) {}

  async getSummary(userId: string) {
    const accounts = await this.tradingAccountsRepository.find({
      where: { userId },
    });

    if (accounts.length === 0) {
      return {
        totalUnrealizedPnl: 0,
        totalPositions: 0,
        accounts: [],
      };
    }

    const accountIds = accounts.map((a) => a.id);
    const positions = await this.positionsRepository.find({
      where: { accountId: In(accountIds) },
    });

    let totalUnrealizedPnl = 0;
    for (const pos of positions) {
      const entry = parseFloat(pos.entryPrice);
      const current = pos.currentPrice ? parseFloat(pos.currentPrice) : entry;
      const size = parseFloat(pos.positionSize);
      const directionMultiplier = pos.direction === 'BUY' ? 1 : -1;
      totalUnrealizedPnl += (current - entry) * size * directionMultiplier;
    }

    return {
      totalUnrealizedPnl,
      totalPositions: positions.length,
      accounts: accounts.map((a) => ({ id: a.id, label: a.label })),
    };
  }

  async getPositions(userId: string) {
    const accounts = await this.tradingAccountsRepository.find({
      where: { userId },
    });

    if (accounts.length === 0) {
      return [];
    }

    const accountIds = accounts.map((a) => a.id);

    // 1. Reconciled positions — only MetaAPI accounts populate this table.
    const positions = await this.positionsRepository.find({
      where: { accountId: In(accountIds) },
      order: { openedAt: 'DESC' },
    });

    // 2. Deriv-direct accounts are skipped by reconciliation (see
    //    reconciliation.service — no MetaAPI ID), so their open positions never
    //    land in the positions table. Read them straight from the trades table
    //    instead: a trade is still open when status='filled' and closed_at IS
    //    NULL. Scoping to Deriv accounts avoids double-counting MetaAPI rows.
    const derivAccountIds = accounts
      .filter((a) => !a.metaapiAccountId)
      .map((a) => a.id);
    const openTrades = derivAccountIds.length
      ? await this.tradesRepository.find({
          where: {
            accountId: In(derivAccountIds),
            status: 'filled',
            closedAt: IsNull(),
          },
          order: { openedAt: 'DESC' },
        })
      : [];

    // Map both sources to a single snake_case shape the dashboard expects
    // (the raw TypeORM entities are camelCase, which the UI can't read).
    const fromPositions = positions.map((p) => {
      const entry = parseFloat(p.entryPrice);
      const current = p.currentPrice != null ? parseFloat(p.currentPrice) : undefined;
      const size = parseFloat(p.positionSize);
      const dir = p.direction === 'BUY' ? 1 : -1;
      const unrealized =
        p.unrealizedPnl != null
          ? parseFloat(p.unrealizedPnl)
          : current != null
            ? (current - entry) * size * dir
            : undefined;
      return {
        id: p.id,
        account_id: p.accountId,
        trade_id: p.tradeId,
        instrument: p.instrument,
        direction: p.direction,
        entry_price: entry,
        current_price: current,
        position_size: size,
        unrealized_pnl: unrealized,
        opened_at: p.openedAt?.toISOString() ?? null,
      };
    });

    const fromTrades = openTrades.map((t) => {
      const entry = parseFloat(t.fillPrice ?? t.entryPrice ?? '0');
      return {
        id: t.id,
        account_id: t.accountId,
        trade_id: t.id,
        instrument: t.instrument ?? '—',
        direction: t.direction,
        entry_price: Number.isFinite(entry) ? entry : 0,
        current_price: undefined, // live price/PnL isn't tracked in this table
        position_size: parseFloat(t.positionSize),
        unrealized_pnl: undefined,
        opened_at: (t.openedAt ?? t.createdAt)?.toISOString() ?? null,
      };
    });

    return [...fromPositions, ...fromTrades];
  }

  async getHistory(userId: string, page: number, limit: number) {
    const accounts = await this.tradingAccountsRepository.find({
      where: { userId },
    });

    if (accounts.length === 0) {
      return { data: [], total: 0, page, limit };
    }

    const accountIds = accounts.map((a) => a.id);
    const [data, total] = await this.tradesRepository.findAndCount({
      where: { accountId: In(accountIds) },
      order: { createdAt: 'DESC' },
      skip: (page - 1) * limit,
      take: limit,
    });

    return { data, total, page, limit };
  }

  @Interval(30_000)
  async syncAccounts() {
    this.logger.debug('Starting account sync cycle');

    let accounts: TradingAccount[];
    try {
      accounts = await this.tradingAccountsRepository.find({
        where: { isActive: true },
      });
    } catch (err) {
      this.logger.warn(`Failed to query active accounts: ${err.message}`);
      return;
    }

    if (accounts.length === 0) {
      this.logger.debug('No active accounts to sync');
      return;
    }

    const engineBaseUrl = process.env.EXECUTION_ENGINE_URL || 'http://execution-engine:8002';
    let successCount = 0;
    const BATCH_SIZE = 5; // Limit concurrent MetaAPI calls to avoid rate limits

    const syncOneAccount = async (account: TradingAccount) => {
      try {
        const [detailsResult, statusResult] = await Promise.allSettled([
          firstValueFrom(
            this.httpService.get(`${engineBaseUrl}/accounts/${account.metaapiAccountId}/details`, { timeout: 30_000 }),
          ),
          firstValueFrom(
            this.httpService.get(`${engineBaseUrl}/accounts/${account.metaapiAccountId}/status`, { timeout: 10_000 }),
          ),
        ]);

        if (detailsResult.status === 'fulfilled') {
          const details = detailsResult.value.data;
          const snapshot = this.snapshotsRepository.create({
            accountId: account.id,
            balance: String(details.balance ?? 0),
            equity: String(details.equity ?? 0),
            unrealizedPnl: String((details.equity ?? 0) - (details.balance ?? 0)),
            openPositions: details.open_positions ?? 0,
            margin: String(details.margin ?? 0),
            freeMargin: String(details.free_margin ?? 0),
            leverage: details.leverage ?? 0,
          });
          await this.snapshotsRepository.save(snapshot);
          successCount++;
        } else {
          this.logger.warn(
            `Failed to fetch details for account ${account.id} (${account.metaapiAccountId}): ${detailsResult.reason?.message}`,
          );
        }

        if (statusResult.status === 'fulfilled') {
          const status = statusResult.value.data;
          const cacheValue = JSON.stringify({
            state: status.state,
            connection_status: status.connection_status,
            metaapiAccountId: account.metaapiAccountId,
            cachedAt: new Date().toISOString(),
          });
          try {
            await this.redis.set(`account:status:${account.id}`, cacheValue, 'EX', 60);
          } catch (redisErr) {
            this.logger.warn(`Redis unavailable for status cache write (account ${account.id}): ${redisErr.message}`);
          }
        } else {
          this.logger.warn(
            `Failed to fetch status for account ${account.id} (${account.metaapiAccountId}): ${statusResult.reason?.message}`,
          );
        }
      } catch (err) {
        this.logger.error(`Unexpected error syncing account ${account.id}: ${err.message}`);
      }
    };

    // Process accounts in batches to avoid overwhelming MetaAPI rate limits
    for (let i = 0; i < accounts.length; i += BATCH_SIZE) {
      const batch = accounts.slice(i, i + BATCH_SIZE);
      await Promise.allSettled(batch.map(syncOneAccount));
    }

    // Invalidate performance cache so fresh snapshot data is used
    if (successCount > 0) {
      try {
        const keys = await this.redis.keys('perf:*');
        if (keys.length > 0) {
          await this.redis.del(...keys);
        }
      } catch (err) {
        this.logger.warn(`Redis unavailable for performance cache invalidation: ${err.message}`);
      }

      // Emit WebSocket event
      try {
        this.tradingGateway.emitAccountSync({ syncedAt: new Date().toISOString() });
      } catch (err) {
        this.logger.warn(`Failed to emit account:sync event: ${err.message}`);
      }
    }

    this.logger.debug(`Account sync cycle complete: ${successCount}/${accounts.length} accounts synced`);
  }
}
