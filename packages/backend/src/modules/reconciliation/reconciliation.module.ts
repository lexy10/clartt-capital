import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { HttpModule } from '@nestjs/axios';

import { ReconciliationReport } from './entities/reconciliation-report.entity';
import { ReconciliationConfig } from './entities/reconciliation-config.entity';
import { Position } from '../trades/entities/position.entity';
import { PortfolioSnapshot } from '../portfolios/entities/portfolio-snapshot.entity';
import { KillSwitch } from '../admin/entities/kill-switch.entity';
import { AuditLog } from '../auth/entities/audit-log.entity';
import { TradingAccount } from '../trades/entities/trading-account.entity';
import { AccountInstrument } from '../instruments/entities/account-instrument.entity';

import { ReconciliationService } from './reconciliation.service';
import { ReconciliationWorker } from './reconciliation.worker';
import { ReconciliationConfigService } from './reconciliation-config.service';
import { StateComparator } from './state-comparator.service';
import { AutoCorrectionService } from './auto-correction.service';
import { ReconciliationMetrics } from './reconciliation-metrics.service';
import { ReconciliationController } from './reconciliation.controller';

@Module({
  imports: [
    TypeOrmModule.forFeature([
      ReconciliationReport,
      ReconciliationConfig,
      Position,
      PortfolioSnapshot,
      KillSwitch,
      AuditLog,
      TradingAccount,
      AccountInstrument,
    ]),
    HttpModule,
  ],
  controllers: [ReconciliationController],
  providers: [
    ReconciliationService,
    ReconciliationWorker,
    ReconciliationConfigService,
    StateComparator,
    AutoCorrectionService,
    ReconciliationMetrics,
  ],
  exports: [ReconciliationService, ReconciliationConfigService],
})
export class ReconciliationModule {}
