import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { HttpModule } from '@nestjs/axios';
import { ScheduleModule } from '@nestjs/schedule';
import { MarketDataService } from './market-data.service';
import { MarketDataController } from './market-data.controller';
import { InternalMarketDataController } from './internal-market-data.controller';
import { Candle } from './entities/candle.entity';
import { InstrumentsModule } from '../instruments/instruments.module';
import { AccountInstrument } from '../instruments/entities/account-instrument.entity';
import { GatewayModule } from '../gateway/gateway.module';
import { StrategiesModule } from '../strategies/strategies.module';
import { CandleSubscriberService } from './candle-subscriber.service';
import { BackfillService } from './backfill.service';
import { CandleRetentionService } from './candle-retention.service';
import { CircuitBreakerModule } from '../../common/circuit-breaker/circuit-breaker.module';

@Module({
  imports: [
    TypeOrmModule.forFeature([Candle, AccountInstrument]),
    InstrumentsModule,
    GatewayModule,
    HttpModule,
    StrategiesModule,
    CircuitBreakerModule,
    ScheduleModule.forRoot(),
  ],
  controllers: [MarketDataController, InternalMarketDataController],
  providers: [MarketDataService, CandleSubscriberService, BackfillService, CandleRetentionService],
  exports: [MarketDataService, BackfillService],
})
export class MarketDataModule {}
