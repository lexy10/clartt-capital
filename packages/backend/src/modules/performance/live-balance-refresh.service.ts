import { Injectable, Inject, Logger, OnModuleInit } from '@nestjs/common';
import { Interval } from '@nestjs/schedule';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import Redis from 'ioredis';
import { REDIS_CLIENT } from '../../common/modules/redis.module';
import { TradingAccount } from '../trades/entities/trading-account.entity';

/**
 * Keeps the Redis `account:liveBalance:{id}` cache warm for Deriv accounts.
 *
 * Why a periodic refresher?
 * - The right-rail Portfolio card + the overview endpoint both need the
 *   live equity number, which only the broker knows. Each Deriv API call
 *   takes ~2 seconds (WebSocket connect + authorize + portfolio). Fetching
 *   on every dashboard request makes the page feel sluggish.
 * - Snapshotting via the execution engine's `start_worker` only runs once;
 *   the 24h TTL keeps it alive, but the value would go stale (no re-pull).
 * - This service fetches every 60 seconds in the background so the cache
 *   is always fresh and the dashboard reads it instantly.
 *
 * Each refresh tick calls the execution engine's `/accounts/deriv/details`
 * endpoint (one network hop per account, all in parallel) and writes the
 * result to Redis with a 24h TTL.
 */
@Injectable()
export class LiveBalanceRefreshService implements OnModuleInit {
  private readonly logger = new Logger(LiveBalanceRefreshService.name);
  private readonly engineUrl =
    process.env.EXECUTION_ENGINE_URL || 'http://execution-engine:8002';

  constructor(
    @InjectRepository(TradingAccount)
    private readonly accountRepo: Repository<TradingAccount>,
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
  ) {}

  /** Trigger an immediate refresh at startup so the dashboard isn't empty
   *  on cold boot. Errors are swallowed — the periodic loop will retry. */
  async onModuleInit(): Promise<void> {
    try {
      await this.refreshAll();
    } catch (err) {
      this.logger.warn(`Initial Deriv balance refresh failed: ${(err as Error).message}`);
    }
  }

  /** Periodic refresh — fires every 60 seconds. */
  @Interval(60_000)
  async tick(): Promise<void> {
    await this.refreshAll();
  }

  /** Iterate every Deriv-direct account and refresh its live balance. */
  async refreshAll(): Promise<void> {
    const accounts = await this.accountRepo.find({
      where: { brokerProvider: 'deriv' },
    });
    const derivAccounts = accounts.filter(
      (a) => a.derivApiToken && a.derivLoginId,
    );
    if (derivAccounts.length === 0) return;

    // All accounts in parallel — the engine handles per-account WS auth.
    await Promise.allSettled(
      derivAccounts.map((acct) => this.refreshOne(acct)),
    );
  }

  private async refreshOne(account: TradingAccount): Promise<void> {
    try {
      const resp = await fetch(`${this.engineUrl}/accounts/deriv/details`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          login_id: account.derivLoginId,
          api_token: account.derivApiToken,
        }),
        signal: AbortSignal.timeout(8000),
      });
      if (!resp.ok) {
        this.logger.debug(
          `Deriv details returned ${resp.status} for ${account.id}`,
        );
        return;
      }
      const data = (await resp.json()) as {
        balance?: number;
        equity?: number;
        open_positions?: number;
      };
      const payload = {
        balance: Number(data?.balance ?? 0),
        equity: Number(data?.equity ?? data?.balance ?? 0),
        open_positions: Number(data?.open_positions ?? 0),
      };
      await this.redis.set(
        `account:liveBalance:${account.id}`,
        JSON.stringify(payload),
        'EX',
        86400, // 24h — periodic ticks keep it warm
      );
    } catch (err) {
      this.logger.debug(
        `Live balance refresh failed for ${account.id}: ${(err as Error).message}`,
      );
    }
  }
}
