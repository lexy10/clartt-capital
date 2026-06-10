import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { HttpModule } from '@nestjs/axios';
import { Strategy } from './entities/strategy.entity';
import { BacktestResult } from './entities/backtest-result.entity';
import { BacktestTrade } from './entities/backtest-trade.entity';
import { StrategiesService } from './strategies.service';
import { StrategiesController } from './strategies.controller';
import { BacktestStreamPublisher } from './backtest-stream.publisher';
import { BacktestResultConsumer } from './backtest-result.consumer';
import { GatewayModule } from '../gateway/gateway.module';
import { InstrumentsModule } from '../instruments/instruments.module';

@Module({
  imports: [TypeOrmModule.forFeature([Strategy, BacktestResult, BacktestTrade]), HttpModule, GatewayModule, InstrumentsModule],
  controllers: [StrategiesController],
  providers: [StrategiesService, BacktestStreamPublisher, BacktestResultConsumer],
  exports: [TypeOrmModule, StrategiesService],
})
export class StrategiesModule {}
