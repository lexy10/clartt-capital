import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import { BadRequestException, HttpException, NotFoundException } from '@nestjs/common';
import { HttpService } from '@nestjs/axios';
import { StrategiesService } from './strategies.service';
import { Strategy } from './entities/strategy.entity';
import { BacktestResult } from './entities/backtest-result.entity';
import { BacktestTrade } from './entities/backtest-trade.entity';
import { BacktestStreamPublisher } from './backtest-stream.publisher';
import { InstrumentsService } from '../instruments/instruments.service';

describe('StrategiesService', () => {
  let service: StrategiesService;
  let strategiesRepo: Record<string, jest.Mock>;
  let backtestResultsRepo: Record<string, jest.Mock>;
  let backtestTradesRepo: Record<string, jest.Mock>;
  let publisher: Record<string, jest.Mock>;
  let instrumentsService: Record<string, jest.Mock>;
  let httpService: Record<string, jest.Mock>;
  let loggerWarnSpy: jest.SpyInstance;

  beforeEach(async () => {
    strategiesRepo = {
      find: jest.fn(),
      findOne: jest.fn(),
    };
    backtestResultsRepo = {
      find: jest.fn(),
      findOne: jest.fn(),
      create: jest.fn((data) => ({ id: 'result-uuid-1', ...data })),
      save: jest.fn((entity) => Promise.resolve({ ...entity })),
      remove: jest.fn().mockResolvedValue(undefined),
    };
    backtestTradesRepo = {
      find: jest.fn(),
      save: jest.fn(),
      create: jest.fn(),
    };
    publisher = {
      publishRequest: jest.fn().mockResolvedValue('stream-msg-id-1'),
    };
    instrumentsService = {
      validateInstrumentSymbol: jest.fn().mockResolvedValue(true),
      findBySymbol: jest.fn().mockResolvedValue(null),
    };
    httpService = {
      get: jest.fn(),
      post: jest.fn(),
      patch: jest.fn(),
      delete: jest.fn(),
    };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        StrategiesService,
        { provide: getRepositoryToken(Strategy), useValue: strategiesRepo },
        { provide: getRepositoryToken(BacktestResult), useValue: backtestResultsRepo },
        { provide: getRepositoryToken(BacktestTrade), useValue: backtestTradesRepo },
        { provide: BacktestStreamPublisher, useValue: publisher },
        { provide: InstrumentsService, useValue: instrumentsService },
        { provide: HttpService, useValue: httpService },
      ],
    }).compile();

    service = module.get<StrategiesService>(StrategiesService);
    loggerWarnSpy = jest.spyOn((service as any).logger, 'warn').mockImplementation();
  });

  describe('runBacktest', () => {
    const mockStrategy: Partial<Strategy> = {
      id: 'strategy-uuid-1',
      name: 'Test Strategy',
      config: { instruments: ['R_75'], timeframes: ['M15'] },
    };

    it('should throw NotFoundException when strategy does not exist', async () => {
      strategiesRepo.findOne.mockResolvedValue(null);

      await expect(
        service.runBacktest('user-1', { strategyId: 'nonexistent-id' }),
      ).rejects.toThrow(NotFoundException);
    });

    it('should create a pending backtest result and publish to Redis stream', async () => {
      strategiesRepo.findOne.mockResolvedValue(mockStrategy);

      const result = await service.runBacktest('user-1', {
        strategyId: 'strategy-uuid-1',
        parameters: { initial_capital: 10000 },
        startDate: '2024-01-01T00:00:00Z',
        endDate: '2024-06-01T00:00:00Z',
      });

      expect(result.status).toBe('pending');
      expect(result.strategyId).toBe('strategy-uuid-1');
      expect(result.userId).toBe('user-1');
      expect(result.winRate).toBeNull();
      expect(result.totalTrades).toBeNull();
      expect(result.tradeResults).toBeNull();

      expect(publisher.publishRequest).toHaveBeenCalledWith(
        expect.objectContaining({
          result_id: result.id,
          strategy_id: 'strategy-uuid-1',
          params: { initial_capital: 10000 },
          start_date: '2024-01-01T00:00:00Z',
          end_date: '2024-06-01T00:00:00Z',
          instrument: 'R_75',
          timeframe: '1h',
        }),
      );
    });

    it('should mark record as failed when Redis publish fails', async () => {
      strategiesRepo.findOne.mockResolvedValue(mockStrategy);
      publisher.publishRequest.mockRejectedValue(new Error('Redis connection refused'));

      const result = await service.runBacktest('user-1', {
        strategyId: 'strategy-uuid-1',
      });

      expect(result.status).toBe('failed');
      expect(result.errorMessage).toBe('Redis connection refused');
      // save called twice: once for initial pending, once for failed update
      expect(backtestResultsRepo.save).toHaveBeenCalledTimes(2);
    });

    it('should handle non-Error exceptions during publish', async () => {
      strategiesRepo.findOne.mockResolvedValue(mockStrategy);
      publisher.publishRequest.mockRejectedValue('string error');

      const result = await service.runBacktest('user-1', {
        strategyId: 'strategy-uuid-1',
      });

      expect(result.status).toBe('failed');
      expect(result.errorMessage).toBe('Failed to publish backtest request');
    });

    it('should use default dates when not provided', async () => {
      strategiesRepo.findOne.mockResolvedValue(mockStrategy);

      await service.runBacktest('user-1', {
        strategyId: 'strategy-uuid-1',
      });

      const publishCall = publisher.publishRequest.mock.calls[0][0];
      expect(publishCall.params).toEqual({});
      expect(publishCall.start_date).toBeDefined();
      expect(publishCall.end_date).toBeDefined();
    });

    it('should use empty object for params when not provided', async () => {
      strategiesRepo.findOne.mockResolvedValue(mockStrategy);

      await service.runBacktest('user-1', {
        strategyId: 'strategy-uuid-1',
      });

      const publishCall = publisher.publishRequest.mock.calls[0][0];
      expect(publishCall.params).toEqual({});
    });

    it('should default instrument to strategy first instrument when not provided', async () => {
      strategiesRepo.findOne.mockResolvedValue(mockStrategy);

      const result = await service.runBacktest('user-1', {
        strategyId: 'strategy-uuid-1',
      });

      expect(instrumentsService.validateInstrumentSymbol).toHaveBeenCalledWith('R_75');
      expect(result.config).toEqual(
        expect.objectContaining({ instrument: 'R_75' }),
      );
    });

    it('should validate provided instrument against active instruments', async () => {
      strategiesRepo.findOne.mockResolvedValue(mockStrategy);

      await service.runBacktest('user-1', {
        strategyId: 'strategy-uuid-1',
        instrument: 'XAUUSD',
      });

      expect(instrumentsService.validateInstrumentSymbol).toHaveBeenCalledWith('XAUUSD');
    });

    it('should throw BadRequestException for invalid instrument', async () => {
      strategiesRepo.findOne.mockResolvedValue(mockStrategy);
      instrumentsService.validateInstrumentSymbol.mockResolvedValue(false);

      await expect(
        service.runBacktest('user-1', {
          strategyId: 'strategy-uuid-1',
          instrument: 'INVALID',
        }),
      ).rejects.toThrow(BadRequestException);

      await expect(
        service.runBacktest('user-1', {
          strategyId: 'strategy-uuid-1',
          instrument: 'INVALID',
        }),
      ).rejects.toThrow("Instrument 'INVALID' is not a registered active instrument");
    });

    it('should include instrument in saved backtest config', async () => {
      strategiesRepo.findOne.mockResolvedValue(mockStrategy);

      const result = await service.runBacktest('user-1', {
        strategyId: 'strategy-uuid-1',
        instrument: 'XAUUSD',
      });

      expect(result.config).toEqual(
        expect.objectContaining({ instrument: 'XAUUSD' }),
      );
    });

    it('should propagate HTTP 429 when backtest queue is full', async () => {
      strategiesRepo.findOne.mockResolvedValue(mockStrategy);
      publisher.publishRequest.mockRejectedValue(
        new HttpException('Backtest queue is full', 429),
      );

      await expect(
        service.runBacktest('user-1', { strategyId: 'strategy-uuid-1' }),
      ).rejects.toThrow(HttpException);

      await expect(
        service.runBacktest('user-1', { strategyId: 'strategy-uuid-1' }),
      ).rejects.toThrow('Backtest queue is full');

      // The pending record should be cleaned up, not marked as failed
      expect(backtestResultsRepo.remove).toHaveBeenCalled();
    });
  });

  describe('updateBacktestStatus', () => {
    it('should update status on an existing record', async () => {
      const existing = { id: 'result-1', status: 'pending', errorMessage: null };
      backtestResultsRepo.findOne.mockResolvedValue(existing);

      await service.updateBacktestStatus('result-1', 'running');

      expect(backtestResultsRepo.save).toHaveBeenCalledWith(
        expect.objectContaining({ status: 'running', errorMessage: null }),
      );
    });

    it('should update status and errorMessage when provided', async () => {
      const existing = { id: 'result-1', status: 'running', errorMessage: null };
      backtestResultsRepo.findOne.mockResolvedValue(existing);

      await service.updateBacktestStatus('result-1', 'failed', 'Something broke');

      expect(backtestResultsRepo.save).toHaveBeenCalledWith(
        expect.objectContaining({ status: 'failed', errorMessage: 'Something broke' }),
      );
    });

    it('should log warning and return when record not found', async () => {
      backtestResultsRepo.findOne.mockResolvedValue(null);

      await service.updateBacktestStatus('nonexistent', 'running');

      expect(backtestResultsRepo.save).not.toHaveBeenCalled();
      expect(loggerWarnSpy).toHaveBeenCalledWith(
        expect.stringContaining('nonexistent'),
      );
    });
  });

  describe('updateBacktestResult', () => {
    it('should update all metrics and set status to completed', async () => {
      const existing = {
        id: 'result-1',
        status: 'running',
        winRate: null,
        maxDrawdown: null,
        sharpeRatio: null,
        profitFactor: null,
        expectancy: null,
        totalTrades: null,
        winningTrades: null,
        losingTrades: null,
        grossProfit: null,
        grossLoss: null,
        netProfit: null,
        equityCurve: null,
        tradeResults: null,
      };
      backtestResultsRepo.findOne.mockResolvedValue(existing);

      await service.updateBacktestResult('result-1', {
        winRate: 0.65,
        maxDrawdown: 1200.5,
        sharpeRatio: 1.85,
        profitFactor: 2.1,
        expectancy: 45.3,
        totalTrades: 42,
        winningTrades: 25,
        losingTrades: 17,
        grossProfit: 5200.0,
        grossLoss: -2476.19,
        netProfit: 2723.81,
        equityCurve: [10000, 10150, 10300],
        tradeResults: [{ direction: 'BUY', profit_loss: 150 }],
      });

      expect(backtestResultsRepo.save).toHaveBeenCalledWith(
        expect.objectContaining({
          status: 'completed',
          winRate: '0.65',
          maxDrawdown: '1200.5',
          sharpeRatio: '1.85',
          profitFactor: '2.1',
          expectancy: '45.3',
          totalTrades: 42,
          winningTrades: 25,
          losingTrades: 17,
          grossProfit: '5200',
          grossLoss: '-2476.19',
          netProfit: '2723.81',
          equityCurve: [10000, 10150, 10300],
          tradeResults: [{ direction: 'BUY', profit_loss: 150 }],
        }),
      );
    });

    it('should log warning and return when record not found', async () => {
      backtestResultsRepo.findOne.mockResolvedValue(null);

      await service.updateBacktestResult('nonexistent', { winRate: 0.5 });

      expect(backtestResultsRepo.save).not.toHaveBeenCalled();
      expect(loggerWarnSpy).toHaveBeenCalledWith(
        expect.stringContaining('nonexistent'),
      );
    });

    it('should only update provided metrics fields', async () => {
      const existing = {
        id: 'result-1',
        status: 'running',
        winRate: null,
        totalTrades: null,
        winningTrades: null,
        losingTrades: null,
      };
      backtestResultsRepo.findOne.mockResolvedValue(existing);

      await service.updateBacktestResult('result-1', {
        totalTrades: 10,
        winRate: 0.7,
      });

      expect(existing.totalTrades).toBe(10);
      expect(existing.winRate).toBe('0.7');
      // Fields not in the metrics object should remain untouched
      expect(existing.winningTrades).toBeNull();
      expect(existing.losingTrades).toBeNull();
    });
  });
});
