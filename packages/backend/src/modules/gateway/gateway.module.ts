import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { TradingGateway } from './trading.gateway';
import { RedisStreamService } from './redis-stream.service';
import { TradingAccount } from '../trades/entities/trading-account.entity';
import { Trade } from '../trades/entities/trade.entity';
import { InstrumentsModule } from '../instruments/instruments.module';
import { AuthModule } from '../auth/auth.module';

@Module({
  // AuthModule exports JwtModule — the gateway needs JwtService to verify
  // socket connection tokens.
  imports: [TypeOrmModule.forFeature([TradingAccount, Trade]), InstrumentsModule, AuthModule],
  providers: [TradingGateway, RedisStreamService],
  exports: [TradingGateway],
})
export class GatewayModule {}
