import { MarketDataService } from './market-data.service';
import { Repository } from 'typeorm';
import { Candle } from './entities/candle.entity';
import { InstrumentsService } from '../instruments/instruments.service';
import { AccountInstrument } from '../instruments/entities/account-instrument.entity';

describe('MarketDataService', () => {
  let service: MarketDataService;
  let mockCandleRepo: Partial<Repository<Candle>>;
  let mockAccountInstrumentRepo: Partial<Repository<AccountInstrument>>;
  let mockInstrumentsService: Partial<InstrumentsService>;

  beforeEach(() => {
    mockCandleRepo = {
      find: jest.fn().mockResolvedValue([]),
      query: jest.fn().mockResolvedValue(undefined),
    };
    mockAccountInstrumentRepo = {
      createQueryBuilder: jest.fn(),
    };
    mockInstrumentsService = {
      findAllActive: jest.fn().mockResolvedValue([]),
    };
    service = new MarketDataService(
      mockCandleRepo as Repository<Candle>,
      mockAccountInstrumentRepo as Repository<AccountInstrument>,
      mockInstrumentsService as InstrumentsService,
    );
  });

  describe('getCandles', () => {
    it('should query repository with correct params and reverse for chronological order', async () => {
      const candle1 = {
        id: '1',
        instrument: 'US30',
        timeframe: '1m',
        open: 34000,
        high: 34050,
        low: 33990,
        close: 34020,
        volume: 100,
        timestamp: new Date('2024-01-01T10:01:00Z'),
      } as Candle;
      const candle2 = {
        id: '2',
        instrument: 'US30',
        timeframe: '1m',
        open: 34020,
        high: 34060,
        low: 34010,
        close: 34050,
        volume: 120,
        timestamp: new Date('2024-01-01T10:00:00Z'),
      } as Candle;

      // Repository returns DESC order (newest first)
      (mockCandleRepo.find as jest.Mock).mockResolvedValue([candle1, candle2]);

      const result = await service.getCandles('US30', '1m', 50);

      expect(mockCandleRepo.find).toHaveBeenCalledWith({
        where: { instrument: 'US30', timeframe: '1m' },
        order: { timestamp: 'DESC' },
        take: 50,
      });
      // Should be reversed to chronological order
      expect(result).toEqual([candle2, candle1]);
    });

    it('should default count to 100', async () => {
      await service.getCandles('US30', '5m');

      expect(mockCandleRepo.find).toHaveBeenCalledWith({
        where: { instrument: 'US30', timeframe: '5m' },
        order: { timestamp: 'DESC' },
        take: 100,
      });
    });

    it('should return empty array when no candles exist', async () => {
      (mockCandleRepo.find as jest.Mock).mockResolvedValue([]);
      const result = await service.getCandles('US30', '1d', 10);
      expect(result).toEqual([]);
    });
  });

  describe('getInstruments', () => {
    it('should return mapped instruments from InstrumentsService', async () => {
      (mockInstrumentsService.findAllActive as jest.Mock).mockResolvedValue([
        { id: '1', symbol: 'US30', displayName: 'Dow Jones', type: 'index', isActive: true },
        { id: '2', symbol: 'XAUUSD', displayName: 'Gold', type: 'commodity', isActive: true },
      ]);

      const result = await service.getInstruments();

      expect(result).toEqual([
        { symbol: 'US30', displayName: 'Dow Jones', type: 'index' },
        { symbol: 'XAUUSD', displayName: 'Gold', type: 'commodity' },
      ]);
    });

    it('should return empty array when no active instruments', async () => {
      (mockInstrumentsService.findAllActive as jest.Mock).mockResolvedValue([]);
      const result = await service.getInstruments();
      expect(result).toEqual([]);
    });
  });

  describe('upsertCandle', () => {
    const candle = {
      instrument: 'US30',
      timeframe: '1m',
      open: 34000,
      high: 34050,
      low: 33990,
      close: 34020,
      volume: 100,
      timestamp: new Date('2024-01-01T10:00:00Z'),
    };

    it('live tick path uses raw upsert that never overwrites completed candles', async () => {
      await service.upsertCandle(candle);

      expect(mockCandleRepo.query).toHaveBeenCalledTimes(1);
      const [sql, params] = (mockCandleRepo.query as jest.Mock).mock.calls[0];
      expect(sql).toContain('ON CONFLICT (instrument, timeframe, timestamp)');
      // The whole point of the raw SQL: a completed candle keeps its values.
      expect(sql).toContain('WHEN candles.completed = true THEN candles.close');
      expect(params).toEqual([
        'US30', '1m', 34000, 34050, 33990, 34020, 100,
        candle.timestamp, false,
      ]);
    });

    it('backfill path (force=true) overwrites via query builder and marks completed', async () => {
      const execute = jest.fn().mockResolvedValue(undefined);
      const qb: any = {
        insert: jest.fn().mockReturnThis(),
        into: jest.fn().mockReturnThis(),
        values: jest.fn().mockReturnThis(),
        orUpdate: jest.fn().mockReturnThis(),
        execute,
      };
      mockCandleRepo.createQueryBuilder = jest.fn().mockReturnValue(qb) as never;

      await service.upsertCandle(candle, true);

      expect(qb.values).toHaveBeenCalledWith({ ...candle, completed: true });
      expect(qb.orUpdate).toHaveBeenCalledWith(
        ['open', 'high', 'low', 'close', 'volume', 'completed'],
        ['instrument', 'timeframe', 'timestamp'],
      );
      expect(execute).toHaveBeenCalled();
    });
  });

  describe('resolveBrokerSymbol', () => {
    it('should return broker symbol when mapping exists', async () => {
      const mockQb = {
        innerJoinAndSelect: jest.fn().mockReturnThis(),
        where: jest.fn().mockReturnThis(),
        getOne: jest.fn().mockResolvedValue({
          brokerSymbol: 'US30.raw',
          instrument: { symbol: 'US30' },
        }),
      };
      (mockAccountInstrumentRepo.createQueryBuilder as jest.Mock).mockReturnValue(mockQb);

      const result = await service.resolveBrokerSymbol('US30');

      expect(result).toBe('US30.raw');
    });

    it('should fall back to canonical symbol when no mapping exists', async () => {
      const mockQb = {
        innerJoinAndSelect: jest.fn().mockReturnThis(),
        where: jest.fn().mockReturnThis(),
        getOne: jest.fn().mockResolvedValue(null),
      };
      (mockAccountInstrumentRepo.createQueryBuilder as jest.Mock).mockReturnValue(mockQb);

      const result = await service.resolveBrokerSymbol('US30');

      expect(result).toBe('US30');
    });
  });
});
