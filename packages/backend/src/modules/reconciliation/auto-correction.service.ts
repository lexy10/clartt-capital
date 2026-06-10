import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Position } from '../trades/entities/position.entity';
import { PortfolioSnapshot } from '../portfolios/entities/portfolio-snapshot.entity';
import { KillSwitch } from '../admin/entities/kill-switch.entity';
import { AuditLog } from '../auth/entities/audit-log.entity';
import {
  CorrectionResult,
  Discrepancy,
  BrokerPosition,
  BrokerAccountInfo,
} from './types';
import { DiscrepancyType } from './types';

@Injectable()
export class AutoCorrectionService {
  private readonly logger = new Logger(AutoCorrectionService.name);

  constructor(
    @InjectRepository(Position)
    private readonly positionRepository: Repository<Position>,
    @InjectRepository(PortfolioSnapshot)
    private readonly snapshotRepository: Repository<PortfolioSnapshot>,
    @InjectRepository(KillSwitch)
    private readonly killSwitchRepository: Repository<KillSwitch>,
    @InjectRepository(AuditLog)
    private readonly auditLogRepository: Repository<AuditLog>,
  ) {}

  async correctPhantomPosition(
    discrepancy: Discrepancy,
    accountId: string,
  ): Promise<CorrectionResult> {
    try {
      if (await this.isKillSwitchActive()) {
        return {
          type: DiscrepancyType.PHANTOM_POSITION,
          success: false,
          before: { positionId: discrepancy.localPositionId },
          after: {},
          error: 'Kill switch is active, skipping auto-correction',
        };
      }

      const position = await this.positionRepository.findOne({
        where: { id: discrepancy.localPositionId, accountId },
      });

      if (!position) {
        return {
          type: DiscrepancyType.PHANTOM_POSITION,
          success: false,
          before: { positionId: discrepancy.localPositionId },
          after: {},
          error: 'Position not found',
        };
      }

      const before = {
        positionId: position.id,
        instrument: position.instrument,
        direction: position.direction,
        positionSize: position.positionSize,
        entryPrice: position.entryPrice,
      };

      await this.positionRepository.remove(position);

      await this.auditLogRepository.save({
        userId: null,
        eventType: 'reconciliation_auto_correction',
        details: {
          correctionType: DiscrepancyType.PHANTOM_POSITION,
          accountId,
          before,
          after: { deleted: true },
        },
        ipAddress: null,
      });

      this.logger.log(
        `Removed phantom position ${position.id} for account ${accountId}`,
      );

      return {
        type: DiscrepancyType.PHANTOM_POSITION,
        success: true,
        before,
        after: { deleted: true },
      };
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Unknown error';
      this.logger.error(
        `Failed to correct phantom position: ${message}`,
        error instanceof Error ? error.stack : undefined,
      );
      return {
        type: DiscrepancyType.PHANTOM_POSITION,
        success: false,
        before: { positionId: discrepancy.localPositionId },
        after: {},
        error: message,
      };
    }
  }

  async correctMissingPosition(
    discrepancy: Discrepancy,
    brokerPosition: BrokerPosition,
    accountId: string,
  ): Promise<CorrectionResult> {
    try {
      if (await this.isKillSwitchActive()) {
        return {
          type: DiscrepancyType.MISSING_POSITION,
          success: false,
          before: {},
          after: {},
          error: 'Kill switch is active, skipping auto-correction',
        };
      }

      const before = { noLocalPosition: true };

      const newPosition = this.positionRepository.create({
        accountId,
        instrument: brokerPosition.symbol,
        direction: brokerPosition.direction,
        positionSize: String(brokerPosition.volume),
        entryPrice: String(brokerPosition.openPrice),
        currentPrice: String(brokerPosition.openPrice),
        unrealizedPnl: String(brokerPosition.profit),
      });

      const saved = await this.positionRepository.save(newPosition);

      const after = {
        positionId: saved.id,
        instrument: saved.instrument,
        direction: saved.direction,
        positionSize: saved.positionSize,
        entryPrice: saved.entryPrice,
      };

      await this.auditLogRepository.save({
        userId: null,
        eventType: 'reconciliation_auto_correction',
        details: {
          correctionType: DiscrepancyType.MISSING_POSITION,
          accountId,
          brokerPositionId: brokerPosition.id,
          before,
          after,
        },
        ipAddress: null,
      });

      this.logger.log(
        `Created missing position ${saved.id} for account ${accountId} from broker position ${brokerPosition.id}`,
      );

      return {
        type: DiscrepancyType.MISSING_POSITION,
        success: true,
        before,
        after,
      };
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Unknown error';
      this.logger.error(
        `Failed to correct missing position: ${message}`,
        error instanceof Error ? error.stack : undefined,
      );
      return {
        type: DiscrepancyType.MISSING_POSITION,
        success: false,
        before: {},
        after: {},
        error: message,
      };
    }
  }

  async correctBalanceDrift(
    discrepancy: Discrepancy,
    brokerAccountInfo: BrokerAccountInfo,
    accountId: string,
  ): Promise<CorrectionResult> {
    try {
      if (await this.isKillSwitchActive()) {
        return {
          type: DiscrepancyType.BALANCE_DRIFT,
          success: false,
          before: {},
          after: {},
          error: 'Kill switch is active, skipping auto-correction',
        };
      }

      const before = {
        localBalance: discrepancy.localValue,
        localEquity: discrepancy.localValue,
        brokerBalance: discrepancy.brokerValue,
      };

      const snapshot = this.snapshotRepository.create({
        accountId,
        balance: String(brokerAccountInfo.balance),
        equity: String(brokerAccountInfo.equity),
        unrealizedPnl: String(brokerAccountInfo.equity - brokerAccountInfo.balance),
        margin: String(brokerAccountInfo.margin),
        freeMargin: String(brokerAccountInfo.freeMargin),
        openPositions: 0,
        leverage: 0,
      });

      const saved = await this.snapshotRepository.save(snapshot);

      const after = {
        snapshotId: saved.id,
        balance: saved.balance,
        equity: saved.equity,
        margin: saved.margin,
        freeMargin: saved.freeMargin,
      };

      await this.auditLogRepository.save({
        userId: null,
        eventType: 'reconciliation_auto_correction',
        details: {
          correctionType: DiscrepancyType.BALANCE_DRIFT,
          accountId,
          before,
          after,
        },
        ipAddress: null,
      });

      this.logger.log(
        `Inserted corrected portfolio snapshot ${saved.id} for account ${accountId}`,
      );

      return {
        type: DiscrepancyType.BALANCE_DRIFT,
        success: true,
        before,
        after,
      };
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Unknown error';
      this.logger.error(
        `Failed to correct balance drift: ${message}`,
        error instanceof Error ? error.stack : undefined,
      );
      return {
        type: DiscrepancyType.BALANCE_DRIFT,
        success: false,
        before: {},
        after: {},
        error: message,
      };
    }
  }

  private async isKillSwitchActive(): Promise<boolean> {
    const killSwitch = await this.killSwitchRepository.findOne({
      where: { id: 1 },
    });
    return killSwitch?.isActive ?? false;
  }
}
