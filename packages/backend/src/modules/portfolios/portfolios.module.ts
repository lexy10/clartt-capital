import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { ScheduleModule } from '@nestjs/schedule';
import { HttpModule } from '@nestjs/axios';
import { PortfolioSnapshot } from './entities/portfolio-snapshot.entity';
import { Position } from '../trades/entities/position.entity';
import { Trade } from '../trades/entities/trade.entity';
import { TradingAccount } from '../trades/entities/trading-account.entity';
import { PortfoliosController } from './portfolios.controller';
import { PortfoliosService } from './portfolios.service';
import { GatewayModule } from '../gateway/gateway.module';
import { PerformanceModule } from '../performance/performance.module';
import { RedisModule } from '../../common/modules/redis.module';

@Module({
  imports: [
    ScheduleModule.forRoot(),
    TypeOrmModule.forFeature([
      PortfolioSnapshot,
      Position,
      Trade,
      TradingAccount,
    ]),
    HttpModule,
    GatewayModule,
    PerformanceModule,
    RedisModule,
  ],
  controllers: [PortfoliosController],
  providers: [PortfoliosService],
  exports: [PortfoliosService],
})
export class PortfoliosModule {}
