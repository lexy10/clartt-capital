import {
  Injectable,
  Inject,
  Logger,
  NotFoundException,
  ForbiddenException,
  ConflictException,
} from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { HttpService } from '@nestjs/axios';
import { firstValueFrom } from 'rxjs';
import Redis from 'ioredis';
import { AutopilotState } from './entities/autopilot-state.entity';
import { TradingAccount } from '../trades/entities/trading-account.entity';
import { Position } from '../trades/entities/position.entity';
import { REDIS_CLIENT } from '../../common/modules/redis.module';
import { TradingGateway } from '../gateway/trading.gateway';
import { EventPublisherService } from '../events/event-publisher.service';
import { TradingEventDto } from '../events/dto/trading-event.dto';
import { TradingEventType } from '../events/enums/trading-event-type.enum';
import { AutopilotStateChangedPayload } from '../events/dto/event-payloads';
import { AutopilotContextSnapshot } from '../events/dto/context-snapshots';

export interface AutopilotStateResult {
  accountId: string;
  enabled: boolean;
  updatedAt: Date;
}

export interface MasterAutopilotResult {
  enabled: boolean;
  updatedAt: Date;
}

@Injectable()
export class AutopilotService {
  private readonly logger = new Logger(AutopilotService.name);
  private readonly REDIS_KEY_PREFIX = 'autopilot:';
  private readonly REDIS_CHANNEL = 'autopilot:channel';
  private readonly REDIS_MASTER_KEY = 'autopilot:master';
  private readonly REDIS_MASTER_CHANNEL = 'autopilot:master:channel';
  private readonly KILL_SWITCH_KEY = 'kill_switch:status';
  private readonly MAX_RETRIES = 3;
  private readonly BASE_DELAY_MS = 100;

  constructor(
    @InjectRepository(AutopilotState)
    private readonly autopilotRepo: Repository<AutopilotState>,
    @InjectRepository(TradingAccount)
    private readonly tradingAccountRepo: Repository<TradingAccount>,
    @InjectRepository(Position)
    private readonly positionRepo: Repository<Position>,
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
    private readonly tradingGateway: TradingGateway,
    private readonly httpService: HttpService,
    private readonly eventPublisher: EventPublisherService,
  ) {}

  async setAutopilotState(
    accountId: string,
    enabled: boolean,
    userId: string,
  ): Promise<AutopilotStateResult> {
    const account = await this.tradingAccountRepo.findOne({
      where: { id: accountId },
    });
    if (!account) {
      throw new NotFoundException('Trading account not found');
    }

    if (account.userId !== userId) {
      throw new ForbiddenException(
        'Not authorized to modify this account\'s autopilot state',
      );
    }

    if (enabled) {
      const killSwitchStatus = await this.redis.get(this.KILL_SWITCH_KEY);
      if (killSwitchStatus === 'active') {
        throw new ConflictException(
          'Cannot enable autopilot while kill switch is active',
        );
      }
    }

    // Persist to PostgreSQL first (source of truth)
    let state = await this.autopilotRepo.findOne({ where: { accountId } });
    if (!state) {
      state = this.autopilotRepo.create({ accountId });
    }
    state.enabled = enabled;
    const saved = await this.autopilotRepo.save(state);

    // Update Redis with retry logic
    const redisValue = enabled ? 'enabled' : 'disabled';
    await this.redisSetWithRetry(
      `${this.REDIS_KEY_PREFIX}${accountId}`,
      redisValue,
    );
    await this.redisPublishWithRetry(
      this.REDIS_CHANNEL,
      JSON.stringify({ accountId, enabled }),
    );

    // Emit WebSocket event scoped to the owning user
    this.tradingGateway.emitAutopilotStateChange(userId, {
      accountId,
      enabled,
      updatedAt: saved.updatedAt.toISOString(),
    });

    // Start or stop the AccountWorker in the execution engine
    await this.syncWorkerState(account, enabled);

    // Publish AutopilotStateChanged event (fire-and-forget)
    this.publishAutopilotEvent(
      'account',
      accountId,
      !enabled,
      enabled,
      userId,
    ).catch(() => {});

    return {
      accountId: saved.accountId,
      enabled: saved.enabled,
      updatedAt: saved.updatedAt,
    };
  }

  async getAutopilotState(accountId: string): Promise<AutopilotStateResult> {
    // Try Redis first (fast path)
    try {
      const redisValue = await this.redis.get(
        `${this.REDIS_KEY_PREFIX}${accountId}`,
      );
      if (redisValue !== null) {
        return {
          accountId,
          enabled: redisValue === 'enabled',
          updatedAt: new Date(),
        };
      }
    } catch (err) {
      this.logger.warn(
        `Redis read failed for autopilot:${accountId}, falling back to PostgreSQL: ${(err as Error).message}`,
      );
    }

    // Fallback to PostgreSQL
    const state = await this.autopilotRepo.findOne({ where: { accountId } });
    if (!state) {
      return {
        accountId,
        enabled: false,
        updatedAt: new Date(),
      };
    }

    return {
      accountId: state.accountId,
      enabled: state.enabled,
      updatedAt: state.updatedAt,
    };
  }

  async syncStatesToRedis(): Promise<void> {
    const states = await this.autopilotRepo.find();
    this.logger.log(
      `Syncing ${states.length} autopilot states to Redis`,
    );

    for (const state of states) {
      const redisValue = state.enabled ? 'enabled' : 'disabled';
      try {
        await this.redis.set(
          `${this.REDIS_KEY_PREFIX}${state.accountId}`,
          redisValue,
        );
      } catch (err) {
        this.logger.error(
          `Failed to sync autopilot state for account ${state.accountId} to Redis: ${(err as Error).message}`,
        );
      }
    }

    // Also sync master autopilot state
    try {
      const masterValue = await this.redis.get(this.REDIS_MASTER_KEY);
      if (masterValue === null) {
        // Default to disabled if not set
        await this.redis.set(this.REDIS_MASTER_KEY, 'disabled');
        this.logger.log('Master autopilot initialized to disabled in Redis');
      }
    } catch (err) {
      this.logger.error(
        `Failed to sync master autopilot state to Redis: ${(err as Error).message}`,
      );
    }

    this.logger.log('Autopilot state sync to Redis complete');
  }

  // --- Master Autopilot ---

  async setMasterAutopilot(enabled: boolean): Promise<MasterAutopilotResult> {
    if (enabled) {
      const killSwitchStatus = await this.redis.get(this.KILL_SWITCH_KEY);
      if (killSwitchStatus === 'active') {
        throw new ConflictException(
          'Cannot enable autopilot while kill switch is active',
        );
      }
    }

    const redisValue = enabled ? 'enabled' : 'disabled';
    await this.redisSetWithRetry(this.REDIS_MASTER_KEY, redisValue);
    await this.redisPublishWithRetry(
      this.REDIS_MASTER_CHANNEL,
      JSON.stringify({ enabled }),
    );

    // Broadcast to all connected clients via WebSocket
    this.tradingGateway.emitMasterAutopilotChange({ enabled, updatedAt: new Date().toISOString() });

    // Publish AutopilotStateChanged event (fire-and-forget)
    this.publishAutopilotEvent(
      'master',
      null,
      !enabled,
      enabled,
      'system',
    ).catch(() => {});

    return { enabled, updatedAt: new Date() };
  }

  async getMasterAutopilot(): Promise<MasterAutopilotResult> {
    try {
      const value = await this.redis.get(this.REDIS_MASTER_KEY);
      return {
        enabled: value === 'enabled',
        updatedAt: new Date(),
      };
    } catch (err) {
      this.logger.warn(
        `Redis read failed for master autopilot: ${(err as Error).message}`,
      );
      return { enabled: false, updatedAt: new Date() };
    }
  }

  private async publishAutopilotEvent(
    scope: 'master' | 'account',
    accountId: string | null,
    previousState: boolean,
    newState: boolean,
    changedBy: string,
  ): Promise<void> {
    try {
      const contextSnapshot = await this.buildAutopilotContextSnapshot();

      const payload: AutopilotStateChangedPayload = {
        scope,
        account_id: accountId,
        previous_state: previousState,
        new_state: newState,
        changed_by: changedBy,
      };

      const aggregateId = scope === 'master'
        ? 'autopilot:master'
        : `autopilot:${accountId}`;

      const event = new TradingEventDto();
      event.event_type = TradingEventType.AutopilotStateChanged;
      event.aggregate_id = aggregateId;
      event.sequence_number = Date.now();
      event.payload = payload as unknown as Record<string, unknown>;
      event.context_snapshot = contextSnapshot as unknown as Record<string, unknown>;

      await this.eventPublisher.publish(event);
    } catch (err) {
      this.logger.error(
        `Failed to publish AutopilotStateChanged event: ${(err as Error).message}`,
      );
    }
  }

  private async buildAutopilotContextSnapshot(): Promise<AutopilotContextSnapshot> {
    const openPositionsPerAccount: Record<string, number> = {};

    try {
      const positions = await this.positionRepo
        .createQueryBuilder('p')
        .select('p.account_id', 'account_id')
        .addSelect('COUNT(*)::int', 'count')
        .groupBy('p.account_id')
        .getRawMany<{ account_id: string; count: number }>();

      for (const row of positions) {
        if (row.account_id) {
          openPositionsPerAccount[row.account_id] = row.count;
        }
      }
    } catch (err) {
      this.logger.warn(
        `Failed to fetch open positions for context snapshot: ${(err as Error).message}`,
      );
    }

    let pendingSignalsCount = 0;
    try {
      pendingSignalsCount = await this.redis.xlen('signals:stream');
    } catch (err) {
      this.logger.warn(
        `Failed to fetch pending signals count for context snapshot: ${(err as Error).message}`,
      );
    }

    let killSwitchActive = false;
    try {
      const ksStatus = await this.redis.get('kill_switch:status');
      killSwitchActive = ksStatus === 'active';
    } catch (err) {
      this.logger.warn(
        `Failed to fetch kill switch status for context snapshot: ${(err as Error).message}`,
      );
    }

    return {
      open_positions_per_account: openPositionsPerAccount,
      pending_signals_count: pendingSignalsCount,
      kill_switch_active: killSwitchActive,
    };
  }

  private async syncWorkerState(
    account: TradingAccount,
    enabled: boolean,
  ): Promise<void> {
    const engineBaseUrl =
      process.env.EXECUTION_ENGINE_URL || 'http://execution-engine:8002';
    try {
      if (enabled) {
        await firstValueFrom(
          this.httpService.post(
            `${engineBaseUrl}/workers/start`,
            {
              account_id: account.id,
              user_id: account.userId,
              metaapi_account_id: account.metaapiAccountId,
              label: account.label || '',
              broker_provider: account.brokerProvider,
              account_kind: account.accountKind,
              deriv_api_token: account.derivApiToken,
              deriv_login_id: account.derivLoginId,
            },
            { timeout: 5000 },
          ),
        );
        this.logger.log(`Started worker for account ${account.id}`);
      } else {
        await firstValueFrom(
          this.httpService.post(
            `${engineBaseUrl}/workers/${account.id}/stop`,
            {},
            { timeout: 5000 },
          ),
        );
        this.logger.log(`Stopped worker for account ${account.id}`);
      }
    } catch (err) {
      this.logger.warn(
        `Failed to sync worker state for account ${account.id}: ${(err as Error).message}`,
      );
    }
  }

  private async redisSetWithRetry(
    key: string,
    value: string,
  ): Promise<void> {
    for (let attempt = 0; attempt < this.MAX_RETRIES; attempt++) {
      try {
        await this.redis.set(key, value);
        return;
      } catch (err) {
        const delay = this.BASE_DELAY_MS * Math.pow(2, attempt);
        this.logger.warn(
          `Redis SET retry ${attempt + 1}/${this.MAX_RETRIES} for key ${key} (delay: ${delay}ms): ${(err as Error).message}`,
        );
        if (attempt < this.MAX_RETRIES - 1) {
          await this.sleep(delay);
        }
      }
    }
    this.logger.error(
      `All ${this.MAX_RETRIES} Redis SET retries failed for key ${key}. PostgreSQL remains source of truth.`,
    );
  }

  private async redisPublishWithRetry(
    channel: string,
    message: string,
  ): Promise<void> {
    for (let attempt = 0; attempt < this.MAX_RETRIES; attempt++) {
      try {
        await this.redis.publish(channel, message);
        return;
      } catch (err) {
        const delay = this.BASE_DELAY_MS * Math.pow(2, attempt);
        this.logger.warn(
          `Redis PUBLISH retry ${attempt + 1}/${this.MAX_RETRIES} for channel ${channel} (delay: ${delay}ms): ${(err as Error).message}`,
        );
        if (attempt < this.MAX_RETRIES - 1) {
          await this.sleep(delay);
        }
      }
    }
    this.logger.error(
      `All ${this.MAX_RETRIES} Redis PUBLISH retries failed for channel ${channel}.`,
    );
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}
