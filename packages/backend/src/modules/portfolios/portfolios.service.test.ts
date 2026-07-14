import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import { HttpService } from '@nestjs/axios';
import { PortfoliosService } from './portfolios.service';
import { Position } from '../trades/entities/position.entity';
import { Trade } from '../trades/entities/trade.entity';
import { TradingAccount } from '../trades/entities/trading-account.entity';
import { PortfolioSnapshot } from './entities/portfolio-snapshot.entity';
import { REDIS_CLIENT } from '../../common/modules/redis.module';
import { TradingGateway } from '../gateway/trading.gateway';
import { PerformanceService } from '../performance/performance.service';

describe('PortfoliosService', () => {
  let service: PortfoliosService;
  let positionsRepo: any;
  let tradesRepo: any;
  let accountsRepo: any;
  let snapshotsRepo: any;

  beforeEach(async () => {
    positionsRepo = { find: jest.fn() };
    tradesRepo = { findAndCount: jest.fn() };
    accountsRepo = { find: jest.fn() };
    snapshotsRepo = { create: jest.fn(), save: jest.fn() };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        PortfoliosService,
        { provide: getRepositoryToken(Position), useValue: positionsRepo },
        { provide: getRepositoryToken(Trade), useValue: tradesRepo },
        { provide: getRepositoryToken(TradingAccount), useValue: accountsRepo },
        { provide: getRepositoryToken(PortfolioSnapshot), useValue: snapshotsRepo },
        { provide: HttpService, useValue: { get: jest.fn(), post: jest.fn() } },
        { provide: REDIS_CLIENT, useValue: { set: jest.fn(), get: jest.fn() } },
        { provide: TradingGateway, useValue: { emitAccountSync: jest.fn() } },
        { provide: PerformanceService, useValue: {} },
      ],
    }).compile();

    service = module.get<PortfoliosService>(PortfoliosService);
  });

  describe('getSummary', () => {
    it('should return empty summary when user has no accounts', async () => {
      accountsRepo.find.mockResolvedValue([]);
      const result = await service.getSummary('user-1');
      expect(result).toEqual({
        totalUnrealizedPnl: 0,
        totalPositions: 0,
        accounts: [],
      });
    });

    it('should compute unrealized P/L for BUY positions', async () => {
      accountsRepo.find.mockResolvedValue([{ id: 'acc-1', label: 'Main' }]);
      positionsRepo.find.mockResolvedValue([
        {
          entryPrice: '100.00000000',
          currentPrice: '110.00000000',
          positionSize: '2.00000000',
          direction: 'BUY',
        },
      ]);

      const result = await service.getSummary('user-1');
      // (110 - 100) * 2 * 1 = 20
      expect(result.totalUnrealizedPnl).toBe(20);
      expect(result.totalPositions).toBe(1);
    });

    it('should compute unrealized P/L for SELL positions', async () => {
      accountsRepo.find.mockResolvedValue([{ id: 'acc-1', label: 'Main' }]);
      positionsRepo.find.mockResolvedValue([
        {
          entryPrice: '100.00000000',
          currentPrice: '90.00000000',
          positionSize: '1.00000000',
          direction: 'SELL',
        },
      ]);

      const result = await service.getSummary('user-1');
      // (90 - 100) * 1 * -1 = 10
      expect(result.totalUnrealizedPnl).toBe(10);
    });

    it('should aggregate across multiple accounts and positions', async () => {
      accountsRepo.find.mockResolvedValue([
        { id: 'acc-1', label: 'A' },
        { id: 'acc-2', label: 'B' },
      ]);
      positionsRepo.find.mockResolvedValue([
        {
          entryPrice: '100.00000000',
          currentPrice: '105.00000000',
          positionSize: '1.00000000',
          direction: 'BUY',
        },
        {
          entryPrice: '200.00000000',
          currentPrice: '190.00000000',
          positionSize: '1.00000000',
          direction: 'SELL',
        },
      ]);

      const result = await service.getSummary('user-1');
      // BUY: (105-100)*1*1 = 5, SELL: (190-200)*1*-1 = 10 => total = 15
      expect(result.totalUnrealizedPnl).toBe(15);
      expect(result.totalPositions).toBe(2);
    });
  });

  describe('getPositions', () => {
    it('should return empty array when user has no accounts', async () => {
      accountsRepo.find.mockResolvedValue([]);
      const result = await service.getPositions('user-1');
      expect(result).toEqual([]);
    });

    it('should return positions for user accounts', async () => {
      accountsRepo.find.mockResolvedValue([{ id: 'acc-1' }]);
      const positions = [{ id: 'pos-1', instrument: 'US30' }];
      positionsRepo.find.mockResolvedValue(positions);

      const result = await service.getPositions('user-1');
      expect(result).toEqual(positions);
    });
  });

  describe('getHistory', () => {
    it('should return empty result when user has no accounts', async () => {
      accountsRepo.find.mockResolvedValue([]);
      const result = await service.getHistory('user-1', 1, 20);
      expect(result).toEqual({ data: [], total: 0, page: 1, limit: 20 });
    });

    it('should return paginated trade history', async () => {
      accountsRepo.find.mockResolvedValue([{ id: 'acc-1' }]);
      const trades = [{ id: 'trade-1' }];
      tradesRepo.findAndCount.mockResolvedValue([trades, 1]);

      const result = await service.getHistory('user-1', 1, 20);
      expect(result.data).toEqual(trades);
      expect(result.total).toBe(1);
      expect(result.page).toBe(1);
      expect(result.limit).toBe(20);
    });

    it('should apply correct pagination offset', async () => {
      accountsRepo.find.mockResolvedValue([{ id: 'acc-1' }]);
      tradesRepo.findAndCount.mockResolvedValue([[], 0]);

      await service.getHistory('user-1', 3, 10);
      expect(tradesRepo.findAndCount).toHaveBeenCalledWith(
        expect.objectContaining({ skip: 20, take: 10 }),
      );
    });
  });

  // recordSnapshots() was replaced by syncAccounts(), which pulls balances
  // from the execution engine over HTTP instead of computing P&L locally.
  // The old test tested removed behavior; syncAccounts needs an HTTP-mocked
  // test of its own (not yet written).
});
