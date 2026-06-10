import { Module, OnModuleInit, Logger } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { HttpModule } from '@nestjs/axios';
import { TradingAccount } from '../trades/entities/trading-account.entity';
import { PortfolioSnapshot } from '../portfolios/entities/portfolio-snapshot.entity';
import { AccountStrategy } from './entities/account-strategy.entity';
import { AccountsController } from './accounts.controller';
import { AccountsService } from './accounts.service';
import { InstrumentsModule } from '../instruments/instruments.module';
import { MarketDataModule } from '../market-data/market-data.module';
import { CircuitBreakerModule } from '../../common/circuit-breaker/circuit-breaker.module';

@Module({
  imports: [
    TypeOrmModule.forFeature([TradingAccount, PortfolioSnapshot, AccountStrategy]),
    HttpModule,
    InstrumentsModule,
    MarketDataModule,
    CircuitBreakerModule,
  ],
  controllers: [AccountsController],
  providers: [AccountsService],
  exports: [AccountsService],
})
export class AccountsModule implements OnModuleInit {
  private readonly logger = new Logger(AccountsModule.name);

  constructor(private readonly accountsService: AccountsService) {}

  async onModuleInit(): Promise<void> {
    await this.accountsService.syncAllAccountStrategiesToRedis();
    this.logger.log('Account-strategy mappings synced to Redis');
    await this.accountsService.syncAllAccountBrokerSymbolsToRedis();
    this.logger.log('Account broker symbol mappings synced to Redis');
  }
}
