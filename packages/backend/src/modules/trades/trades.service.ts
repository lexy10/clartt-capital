import { Injectable, NotFoundException, Inject, forwardRef, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository, In } from 'typeorm';
import { Trade } from './entities/trade.entity';
import { TradingAccount } from './entities/trading-account.entity';
import { PerformanceService } from '../performance/performance.service';

@Injectable()
export class TradesService {
  private readonly logger = new Logger(TradesService.name);

  constructor(
    @InjectRepository(Trade)
    private readonly tradesRepository: Repository<Trade>,
    @InjectRepository(TradingAccount)
    private readonly tradingAccountsRepository: Repository<TradingAccount>,
    @Inject(forwardRef(() => PerformanceService))
    private readonly performanceService: PerformanceService,
  ) {}

  private async getUserAccountIds(userId: string): Promise<string[]> {
    const accounts = await this.tradingAccountsRepository.find({
      where: { userId },
    });
    return accounts.map((a) => a.id);
  }

  async findAll(userId: string, query: { limit?: number; offset?: number }) {
    const accountIds = await this.getUserAccountIds(userId);
    if (accountIds.length === 0) {
      return { data: [], total: 0, limit: query.limit ?? 50, offset: query.offset ?? 0 };
    }

    const limit = query.limit ?? 50;
    const offset = query.offset ?? 0;

    const [data, total] = await this.tradesRepository.findAndCount({
      where: { accountId: In(accountIds) },
      order: { createdAt: 'DESC' },
      take: limit,
      skip: offset,
    });

    return { data, total, limit, offset };
  }

  async findById(userId: string, id: string) {
    const accountIds = await this.getUserAccountIds(userId);
    if (accountIds.length === 0) {
      throw new NotFoundException(`Trade with id ${id} not found`);
    }

    const trade = await this.tradesRepository.findOne({
      where: { id, accountId: In(accountIds) },
    });

    if (!trade) {
      throw new NotFoundException(`Trade with id ${id} not found`);
    }

    return trade;
  }

  async recordTrade(trade: Partial<Trade>): Promise<Trade> {
    const saved = await this.tradesRepository.save(
      this.tradesRepository.create(trade),
    );

    // Invalidate performance cache for the owning user
    if (saved.accountId) {
      try {
        const account = await this.tradingAccountsRepository.findOne({
          where: { id: saved.accountId },
        });
        if (account) {
          await this.performanceService.invalidateUserCache(account.userId);
        }
      } catch (err) {
        this.logger.warn(
          `Failed to invalidate performance cache after recording trade: ${(err as Error).message}`,
        );
      }
    }

    return saved;
  }
}
