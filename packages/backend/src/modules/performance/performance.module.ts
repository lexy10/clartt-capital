import { Module } from '@nestjs/common';
import { ScheduleModule } from '@nestjs/schedule';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Trade } from '../trades/entities/trade.entity';
import { Signal } from '../signals/entities/signal.entity';
import { TradingAccount } from '../trades/entities/trading-account.entity';
import { PortfolioSnapshot } from '../portfolios/entities/portfolio-snapshot.entity';
import { Strategy } from '../strategies/entities/strategy.entity';
import { PerformanceService } from './performance.service';
import { PerformanceController } from './performance.controller';
import { LiveBalanceRefreshService } from './live-balance-refresh.service';

@Module({
  imports: [
    ScheduleModule.forRoot(),
    TypeOrmModule.forFeature([Trade, Signal, TradingAccount, PortfolioSnapshot, Strategy]),
  ],
  controllers: [PerformanceController],
  providers: [PerformanceService, LiveBalanceRefreshService],
  exports: [PerformanceService],
})
export class PerformanceModule {}
