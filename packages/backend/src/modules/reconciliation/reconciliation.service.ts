import {
  Inject,
  Injectable,
  Logger,
  Optional,
} from '@nestjs/common';
import { HttpService } from '@nestjs/axios';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import Redis from 'ioredis';
import { firstValueFrom } from 'rxjs';

import { REDIS_CLIENT } from '../../common/modules/redis.module';
import { TradingAccount } from '../trades/entities/trading-account.entity';
import { Position } from '../trades/entities/position.entity';
import { PortfolioSnapshot } from '../portfolios/entities/portfolio-snapshot.entity';
import { AccountInstrument } from '../instruments/entities/account-instrument.entity';
import { KillSwitch } from '../admin/entities/kill-switch.entity';
import { ReconciliationReport } from './entities/reconciliation-report.entity';
import { StateComparator } from './state-comparator.service';
import { AutoCorrectionService } from './auto-correction.service';
import { ReconciliationConfigService } from './reconciliation-config.service';
import { ReconciliationMetrics } from './reconciliation-metrics.service';
import { TradingGateway } from '../gateway/trading.gateway';
import {
  BrokerPosition,
  BrokerAccountInfo,
  LocalPositionState,
  SymbolMapping,
  Discrepancy,
  CorrectionResult,
  EffectiveConfig,
  ReconciliationDiscrepancyPayload,
} from './types';
import { DiscrepancyType, DiscrepancySeverity } from './types';

const EXECUTION_ENGINE_URL =
  process.env.EXECUTION_ENGINE_URL || 'http://execution-engine:8002';

const REDIS_STATE_PREFIX = 'reconciliation:state:';
const REDIS_ALERTS_CHANNEL = 'reconciliation:alerts';

@Injectable()
export class ReconciliationService {
  private readonly logger = new Logger(ReconciliationService.name);

  constructor(
    private readonly httpService: HttpService,
    @InjectRepository(ReconciliationReport)
    private readonly reportRepo: Repository<ReconciliationReport>,
    @InjectRepository(Position)
    private readonly positionRepo: Repository<Position>,
    @InjectRepository(PortfolioSnapshot)
    private readonly snapshotRepo: Repository<PortfolioSnapshot>,
    @InjectRepository(AccountInstrument)
    private readonly accountInstrumentRepo: Repository<AccountInstrument>,
    @InjectRepository(KillSwitch)
    private readonly killSwitchRepo: Repository<KillSwitch>,
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
    private readonly stateComparator: StateComparator,
    private readonly autoCorrectionService: AutoCorrectionService,
    private readonly configService: ReconciliationConfigService,
    private readonly metrics: ReconciliationMetrics,
    @Optional() @Inject(TradingGateway)
    private readonly tradingGateway?: TradingGateway,
  ) {}

  /**
   * Orchestrate a full reconciliation cycle for a single account.
   */
  async reconcileAccount(account: TradingAccount): Promise<void> {
    const startTime = Date.now();
    const cycleTimestamp = new Date();
    const accountId = account.id;

    try {
      // 1. Fetch broker state
      // Skip reconciliation for Deriv-direct accounts (no MetaAPI ID — TODO: add Deriv reconciliation)
      if (!account.metaapiAccountId) {
        this.logger.debug(`Skipping reconciliation for non-MetaAPI account ${accountId}`);
        return;
      }
      const metaapiId: string = account.metaapiAccountId;
      let brokerPositions: BrokerPosition[];
      let brokerAccountInfo: BrokerAccountInfo;
      try {
        [brokerAccountInfo, brokerPositions] = await Promise.all([
          this.fetchBrokerAccountInfo(metaapiId),
          this.fetchBrokerPositions(metaapiId),
        ]);
      } catch (error) {
        await this.handleBrokerUnreachable(accountId, cycleTimestamp, startTime, error);
        return;
      }

      // 2. Query local state
      const [localPositions, latestSnapshot, symbolMappings] = await Promise.all([
        this.queryLocalPositions(accountId),
        this.queryLatestSnapshot(accountId),
        this.querySymbolMappings(accountId),
      ]);

      // 3. Check kill switch
      const killSwitchActive = await this.isKillSwitchActive();

      // 4. Load effective config
      const config = await this.configService.getEffectiveConfig(accountId);

      // 5. Compare positions and balances
      const positionDiscrepancies = this.stateComparator.comparePositions(
        localPositions,
        brokerPositions,
        symbolMappings,
        { positionSizeDrift: config.positionSizeDriftThreshold },
      );

      const balanceDiscrepancies = this.stateComparator.compareBalances(
        latestSnapshot ? { balance: latestSnapshot.balance, equity: latestSnapshot.equity } : null,
        brokerAccountInfo,
        { balanceDrift: config.balanceDriftThreshold, equityDrift: config.equityDriftThreshold },
      );

      let allDiscrepancies = [...positionDiscrepancies, ...balanceDiscrepancies];

      // 6. Track consecutive discrepancies and escalate severity
      allDiscrepancies = await this.trackAndEscalateDiscrepancies(
        accountId,
        allDiscrepancies,
        config,
      );

      // 7. Auto-correction (if enabled and kill switch inactive)
      const corrections: CorrectionResult[] = [];
      if (!killSwitchActive) {
        const correctionResults = await this.applyAutoCorrections(
          allDiscrepancies,
          brokerPositions,
          brokerAccountInfo,
          accountId,
          config,
        );
        corrections.push(...correctionResults);
      }

      // 8. Persist report
      const durationMs = Date.now() - startTime;
      const status = allDiscrepancies.length > 0 ? 'discrepancies_found' : 'clean';

      const report = await this.persistReport({
        accountId,
        cycleTimestamp,
        discrepancies: allDiscrepancies,
        autoCorrectionsApplied: corrections,
        brokerStateSnapshot: { positions: brokerPositions, accountInfo: brokerAccountInfo },
        localStateSnapshot: {
          positions: localPositions,
          balance: latestSnapshot?.balance ?? null,
          equity: latestSnapshot?.equity ?? null,
        },
        durationMs,
        status,
        errorMessage: null,
      });

      // 9. Emit alerts if discrepancies found
      if (allDiscrepancies.length > 0) {
        await this.emitAlerts(accountId, allDiscrepancies);
      }

      // 10. Update Prometheus metrics
      this.updateMetrics(accountId, status, allDiscrepancies, corrections, durationMs);

      // 11. Update per-account Redis state
      await this.updateRedisState(accountId, status, cycleTimestamp);

      this.logger.log(
        `Reconciliation cycle completed for account ${accountId}: ${status} (${durationMs}ms, ${allDiscrepancies.length} discrepancies)`,
      );
    } catch (error) {
      const durationMs = Date.now() - startTime;
      const message = error instanceof Error ? error.message : 'Unknown error';
      this.logger.error(
        `Reconciliation cycle failed for account ${accountId}: ${message}`,
        error instanceof Error ? error.stack : undefined,
      );

      // Persist error report
      try {
        await this.persistReport({
          accountId,
          cycleTimestamp,
          discrepancies: [],
          autoCorrectionsApplied: [],
          brokerStateSnapshot: {},
          localStateSnapshot: {},
          durationMs,
          status: 'error',
          errorMessage: message,
        });
      } catch (reportError) {
        this.logger.error(
          `Failed to persist error report for account ${accountId}`,
          reportError instanceof Error ? reportError.stack : undefined,
        );
      }

      // Update metrics and Redis state even on error
      this.metrics.incrementCycles(accountId, 'error');
      this.metrics.observeCycleDuration(accountId, durationMs / 1000);
      await this.incrementConsecutiveFailures(accountId);
    }
  }

  /**
   * Purge reconciliation reports older than the retention period.
   */
  async purgeExpiredReports(retentionDays: number = 90): Promise<number> {
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - retentionDays);

    const result = await this.reportRepo
      .createQueryBuilder()
      .delete()
      .from(ReconciliationReport)
      .where('cycle_timestamp < :cutoff', { cutoff })
      .execute();

    const deleted = result.affected ?? 0;
    if (deleted > 0) {
      this.logger.log(`Purged ${deleted} expired reconciliation reports (older than ${retentionDays} days)`);
    }
    return deleted;
  }

  // ─── Private: Broker Communication ────────────────────────────────

  private async fetchBrokerAccountInfo(metaapiAccountId: string): Promise<BrokerAccountInfo> {
    const url = `${EXECUTION_ENGINE_URL}/accounts/${metaapiAccountId}/details`;
    const response = await firstValueFrom(
      this.httpService.get<BrokerAccountInfo>(url, { timeout: 30000 }),
    );
    return response.data;
  }

  private async fetchBrokerPositions(metaapiAccountId: string): Promise<BrokerPosition[]> {
    const url = `${EXECUTION_ENGINE_URL}/accounts/${metaapiAccountId}/positions`;
    const response = await firstValueFrom(
      this.httpService.get<{ positions: BrokerPosition[] }>(url, { timeout: 30000 }),
    );
    return response.data.positions ?? [];
  }

  // ─── Private: Local State Queries ─────────────────────────────────

  private async queryLocalPositions(accountId: string): Promise<LocalPositionState[]> {
    const positions = await this.positionRepo.find({
      where: { accountId },
    });
    return positions.map((p) => ({
      id: p.id,
      instrument: p.instrument,
      direction: p.direction,
      positionSize: p.positionSize,
      entryPrice: p.entryPrice,
    }));
  }

  private async queryLatestSnapshot(accountId: string): Promise<PortfolioSnapshot | null> {
    return this.snapshotRepo.findOne({
      where: { accountId },
      order: { snapshotAt: 'DESC' },
    });
  }

  private async querySymbolMappings(accountId: string): Promise<SymbolMapping[]> {
    const accountInstruments = await this.accountInstrumentRepo.find({
      where: { accountId },
      relations: ['instrument'],
    });
    return accountInstruments.map((ai) => ({
      localSymbol: ai.instrument.symbol,
      brokerSymbol: ai.brokerSymbol,
    }));
  }

  private async isKillSwitchActive(): Promise<boolean> {
    const killSwitch = await this.killSwitchRepo.findOne({
      where: { id: 1 },
    });
    return killSwitch?.isActive ?? false;
  }

  // ─── Private: Discrepancy Tracking & Escalation ───────────────────

  private async trackAndEscalateDiscrepancies(
    accountId: string,
    discrepancies: Discrepancy[],
    config: EffectiveConfig,
  ): Promise<Discrepancy[]> {
    const stateKey = `${REDIS_STATE_PREFIX}${accountId}`;

    // Group current discrepancies by type
    const currentTypes = new Set(discrepancies.map((d) => d.type));

    // For each discrepancy type, track consecutive count
    for (const type of Object.values(DiscrepancyType)) {
      const field = `consecutive_discrepancies:${type}`;
      if (currentTypes.has(type)) {
        try {
          await this.redis.hincrby(stateKey, field, 1);
        } catch (err) {
          this.logger.warn(`Redis hincrby failed for ${stateKey}:${field}: ${err}`);
        }
      } else {
        // Reset consecutive count when discrepancy type is no longer present
        try {
          await this.redis.hset(stateKey, field, '0');
        } catch (err) {
          this.logger.warn(`Redis hset failed for ${stateKey}:${field}: ${err}`);
        }
      }
    }

    // Read back consecutive counts and escalate severity
    let consecutiveCounts: Record<string, string> = {};
    try {
      consecutiveCounts = await this.redis.hgetall(stateKey);
    } catch (err) {
      this.logger.warn(`Redis hgetall failed for ${stateKey}: ${err}`);
    }

    return discrepancies.map((d) => {
      const field = `consecutive_discrepancies:${d.type}`;
      const consecutiveCount = parseInt(consecutiveCounts[field] || '0', 10);

      // Update persistent discrepancies gauge
      if (consecutiveCount > 0) {
        this.metrics.setPersistentDiscrepancies(accountId, d.type, consecutiveCount);
      }

      // Escalate warning → critical after escalation_cycle_count consecutive cycles
      if (
        d.severity === DiscrepancySeverity.WARNING &&
        consecutiveCount >= config.escalationCycleCount
      ) {
        return { ...d, severity: DiscrepancySeverity.CRITICAL };
      }

      return d;
    });
  }

  // ─── Private: Auto-Correction ─────────────────────────────────────

  private async applyAutoCorrections(
    discrepancies: Discrepancy[],
    brokerPositions: BrokerPosition[],
    brokerAccountInfo: BrokerAccountInfo,
    accountId: string,
    config: EffectiveConfig,
  ): Promise<CorrectionResult[]> {
    const corrections: CorrectionResult[] = [];

    for (const discrepancy of discrepancies) {
      try {
        let result: CorrectionResult | null = null;

        if (
          discrepancy.type === DiscrepancyType.PHANTOM_POSITION &&
          config.autoCorrectPhantomPositions
        ) {
          result = await this.autoCorrectionService.correctPhantomPosition(
            discrepancy,
            accountId,
          );
        } else if (
          discrepancy.type === DiscrepancyType.MISSING_POSITION &&
          config.autoCorrectMissingPositions
        ) {
          const brokerPos = brokerPositions.find(
            (bp) => bp.id === discrepancy.brokerPositionId,
          );
          if (brokerPos) {
            result = await this.autoCorrectionService.correctMissingPosition(
              discrepancy,
              brokerPos,
              accountId,
            );
          }
        } else if (
          discrepancy.type === DiscrepancyType.BALANCE_DRIFT &&
          config.autoCorrectBalanceDrift
        ) {
          result = await this.autoCorrectionService.correctBalanceDrift(
            discrepancy,
            brokerAccountInfo,
            accountId,
          );
        }

        if (result) {
          corrections.push(result);
          if (result.success) {
            this.metrics.incrementAutoCorrections(accountId, discrepancy.type);
          }
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Unknown error';
        this.logger.error(
          `Auto-correction failed for ${discrepancy.type} on account ${accountId}: ${message}`,
        );
        corrections.push({
          type: discrepancy.type,
          success: false,
          before: {},
          after: {},
          error: message,
        });
      }
    }

    return corrections;
  }

  // ─── Private: Report Persistence ──────────────────────────────────

  private async persistReport(data: {
    accountId: string;
    cycleTimestamp: Date;
    discrepancies: Discrepancy[];
    autoCorrectionsApplied: CorrectionResult[];
    brokerStateSnapshot: Record<string, unknown>;
    localStateSnapshot: Record<string, unknown>;
    durationMs: number;
    status: string;
    errorMessage: string | null;
  }): Promise<ReconciliationReport> {
    const report = this.reportRepo.create({
      accountId: data.accountId,
      cycleTimestamp: data.cycleTimestamp,
      discrepancies: data.discrepancies as unknown as Record<string, unknown>[],
      autoCorrectionsApplied: data.autoCorrectionsApplied as unknown as Record<string, unknown>[],
      brokerStateSnapshot: data.brokerStateSnapshot,
      localStateSnapshot: data.localStateSnapshot,
      durationMs: data.durationMs,
      status: data.status,
      errorMessage: data.errorMessage,
    });
    return this.reportRepo.save(report);
  }

  // ─── Private: Alert Emission ──────────────────────────────────────

  private async emitAlerts(
    accountId: string,
    discrepancies: Discrepancy[],
  ): Promise<void> {
    const payload: ReconciliationDiscrepancyPayload = {
      accountId,
      timestamp: new Date().toISOString(),
      discrepancies: discrepancies.map((d) => ({
        type: d.type,
        severity: d.severity,
        details: d.details,
      })),
    };

    // Socket.IO emission (optional dependency)
    if (this.tradingGateway) {
      try {
        this.tradingGateway.emitReconciliationDiscrepancy(payload);
      } catch (err) {
        this.logger.warn(`Socket.IO emission failed for reconciliation alert: ${err}`);
      }
    }

    // Redis pub/sub
    try {
      await this.redis.publish(REDIS_ALERTS_CHANNEL, JSON.stringify(payload));
    } catch (err) {
      this.logger.warn(`Redis pub/sub publish failed for reconciliation alert: ${err}`);
    }
  }

  // ─── Private: Metrics ─────────────────────────────────────────────

  private updateMetrics(
    accountId: string,
    status: string,
    discrepancies: Discrepancy[],
    corrections: CorrectionResult[],
    durationMs: number,
  ): void {
    this.metrics.incrementCycles(accountId, status);
    this.metrics.observeCycleDuration(accountId, durationMs / 1000);

    for (const d of discrepancies) {
      this.metrics.incrementDiscrepancies(accountId, d.type);
    }
  }

  // ─── Private: Redis State Management ──────────────────────────────

  private async updateRedisState(
    accountId: string,
    status: string,
    cycleTimestamp: Date,
  ): Promise<void> {
    const stateKey = `${REDIS_STATE_PREFIX}${accountId}`;
    try {
      await this.redis.hset(stateKey, {
        last_cycle_at: cycleTimestamp.toISOString(),
        last_status: status,
        consecutive_failures: '0',
      });
    } catch (err) {
      this.logger.warn(`Redis state update failed for account ${accountId}: ${err}`);
    }
  }

  private async incrementConsecutiveFailures(accountId: string): Promise<void> {
    const stateKey = `${REDIS_STATE_PREFIX}${accountId}`;
    try {
      await this.redis.hincrby(stateKey, 'consecutive_failures', 1);
      await this.redis.hset(stateKey, 'last_status', 'error');
    } catch (err) {
      this.logger.warn(`Redis consecutive_failures increment failed for account ${accountId}: ${err}`);
    }
  }

  // ─── Private: Broker Unreachable Handler ──────────────────────────

  private async handleBrokerUnreachable(
    accountId: string,
    cycleTimestamp: Date,
    startTime: number,
    error: unknown,
  ): Promise<void> {
    const durationMs = Date.now() - startTime;
    const message = error instanceof Error ? error.message : 'Unknown error';

    this.logger.warn(
      `Broker unreachable for account ${accountId}: ${message}`,
    );

    // Persist broker_unreachable report
    try {
      await this.persistReport({
        accountId,
        cycleTimestamp,
        discrepancies: [],
        autoCorrectionsApplied: [],
        brokerStateSnapshot: {},
        localStateSnapshot: {},
        durationMs,
        status: 'broker_unreachable',
        errorMessage: message,
      });
    } catch (reportError) {
      this.logger.error(
        `Failed to persist broker_unreachable report for account ${accountId}`,
        reportError instanceof Error ? reportError.stack : undefined,
      );
    }

    // Update metrics and Redis state
    this.metrics.incrementCycles(accountId, 'broker_unreachable');
    this.metrics.observeCycleDuration(accountId, durationMs / 1000);
    await this.incrementConsecutiveFailures(accountId);
  }
}
