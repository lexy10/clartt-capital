import { Injectable, Inject, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { HttpService } from '@nestjs/axios';
import { firstValueFrom } from 'rxjs';
import Redis from 'ioredis';
import { KillSwitch } from './entities/kill-switch.entity';
import { REDIS_CLIENT } from '../../common/modules/redis.module';
import { AccountsService } from '../accounts/accounts.service';
import { EventPublisherService } from '../events/event-publisher.service';
import { TradingEventDto } from '../events/dto/trading-event.dto';
import { TradingEventType } from '../events/enums/trading-event-type.enum';
import {
  KillSwitchActivatedPayload,
  KillSwitchDeactivatedPayload,
} from '../events/dto/event-payloads';

@Injectable()
export class AdminService {
  private readonly logger = new Logger(AdminService.name);
  private readonly REDIS_KEY = 'kill_switch:status';
  private readonly REDIS_CHANNEL = 'kill_switch:channel';
  private readonly engineBaseUrl: string;

  constructor(
    @InjectRepository(KillSwitch)
    private readonly killSwitchRepo: Repository<KillSwitch>,
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
    private readonly httpService: HttpService,
    private readonly accountsService: AccountsService,
    private readonly eventPublisher: EventPublisherService,
  ) {
    this.engineBaseUrl =
      process.env.EXECUTION_ENGINE_URL || 'http://execution-engine:8002';
  }

  async activateKillSwitch(userId: string, mode: 'soft' | 'hard' = 'soft'): Promise<KillSwitch> {
    let ks = await this.killSwitchRepo.findOne({ where: { id: 1 } });
    if (!ks) {
      ks = this.killSwitchRepo.create({ id: 1 });
    }

    ks.isActive = true;
    ks.activatedBy = userId;
    ks.activatedAt = new Date();
    ks.deactivatedAt = null;

    const saved = await this.killSwitchRepo.save(ks);

    await this.redis.set(this.REDIS_KEY, 'active');
    await this.redis.publish(this.REDIS_CHANNEL, 'active');

    // Fire-and-forget: publish KillSwitchActivated event
    this.publishKillSwitchActivatedEvent(userId, mode).catch((err) => {
      this.logger.error(
        `Failed to publish KillSwitchActivated event: ${(err as Error).message}`,
      );
    });

    if (mode === 'hard') {
      // Close all open positions across all active accounts (fire-and-forget)
      this.closeAllPositions().catch((err) => {
        this.logger.error(`Kill switch close-all-positions failed: ${err.message}`);
      });
    }

    this.logger.log(`Kill switch activated (mode=${mode}) by user ${userId}`);
    return saved;
  }

  /**
   * Close all open positions across all active trading accounts.
   * Called when the kill switch is activated.
   */
  private async closeAllPositions(): Promise<void> {
    const accounts = await this.accountsService.findAllActive();
    this.logger.log(`Kill switch: closing positions for ${accounts.length} active account(s)`);

    for (const account of accounts) {
      try {
        const { data } = await firstValueFrom(
          this.httpService.post(
            `${this.engineBaseUrl}/workers/close-all-positions`,
            { metaapi_account_id: account.metaapiAccountId },
            { timeout: 30000 },
          ),
        );
        this.logger.log(
          `Kill switch: account ${account.id} — closed ${data.closed}/${data.positions_found} positions (${data.failed} failed)`,
        );
      } catch (err) {
        this.logger.error(
          `Kill switch: failed to close positions for account ${account.id}: ${(err as Error).message}`,
        );
      }
    }
  }

  async deactivateKillSwitch(userId: string): Promise<KillSwitch> {
    let ks = await this.killSwitchRepo.findOne({ where: { id: 1 } });
    if (!ks) {
      ks = this.killSwitchRepo.create({ id: 1 });
    }

    const activatedAt = ks.activatedAt;

    ks.isActive = false;
    ks.activatedBy = null;
    ks.deactivatedAt = new Date();

    const saved = await this.killSwitchRepo.save(ks);

    await this.redis.set(this.REDIS_KEY, 'inactive');
    await this.redis.publish(this.REDIS_CHANNEL, 'inactive');

    // Fire-and-forget: publish KillSwitchDeactivated event
    const durationActiveSeconds = activatedAt
      ? Math.round((saved.deactivatedAt!.getTime() - activatedAt.getTime()) / 1000)
      : 0;
    this.publishKillSwitchDeactivatedEvent(userId, durationActiveSeconds).catch(
      (err) => {
        this.logger.error(
          `Failed to publish KillSwitchDeactivated event: ${(err as Error).message}`,
        );
      },
    );

    return saved;
  }

  async getStatus(): Promise<{
    killSwitch: { isActive: boolean; activatedBy: string | null; activatedAt: Date | null; deactivatedAt: Date | null };
    system: { uptime: number; timestamp: string };
  }> {
    let ks = await this.killSwitchRepo.findOne({ where: { id: 1 } });
    if (!ks) {
      ks = this.killSwitchRepo.create({ id: 1, isActive: false });
      ks = await this.killSwitchRepo.save(ks);
    }

    return {
      killSwitch: {
        isActive: ks.isActive,
        activatedBy: ks.activatedBy,
        activatedAt: ks.activatedAt,
        deactivatedAt: ks.deactivatedAt,
      },
      system: {
        uptime: process.uptime(),
        timestamp: new Date().toISOString(),
      },
    };
  }

  private async publishKillSwitchActivatedEvent(
    activatedBy: string,
    reason: string,
  ): Promise<void> {
    const payload: KillSwitchActivatedPayload = {
      activated_by: activatedBy,
      reason,
    };

    const event = new TradingEventDto();
    event.event_type = TradingEventType.KillSwitchActivated;
    event.aggregate_id = 'kill_switch:1';
    event.sequence_number = Date.now();
    event.payload = payload as unknown as Record<string, unknown>;

    await this.eventPublisher.publish(event);
  }

  private async publishKillSwitchDeactivatedEvent(
    deactivatedBy: string,
    durationActiveSeconds: number,
  ): Promise<void> {
    const payload: KillSwitchDeactivatedPayload = {
      deactivated_by: deactivatedBy,
      duration_active_seconds: durationActiveSeconds,
    };

    const event = new TradingEventDto();
    event.event_type = TradingEventType.KillSwitchDeactivated;
    event.aggregate_id = 'kill_switch:1';
    event.sequence_number = Date.now();
    event.payload = payload as unknown as Record<string, unknown>;

    await this.eventPublisher.publish(event);
  }
}
