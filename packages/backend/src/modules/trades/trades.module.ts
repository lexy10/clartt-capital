import { Module, forwardRef } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { TradingAccount } from './entities/trading-account.entity';
import { Trade } from './entities/trade.entity';
import { Position } from './entities/position.entity';
import { TradesService } from './trades.service';
import { TradesController } from './trades.controller';
import { PerformanceModule } from '../performance/performance.module';

@Module({
  imports: [
    TypeOrmModule.forFeature([TradingAccount, Trade, Position]),
    forwardRef(() => PerformanceModule),
  ],
  controllers: [TradesController],
  providers: [TradesService],
  exports: [TypeOrmModule, TradesService],
})
export class TradesModule {}
