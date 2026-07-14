import { CandleSubscriberService } from './candle-subscriber.service';
import { MarketDataService } from './market-data.service';
import { TradingGateway } from '../gateway/trading.gateway';

// Mock ioredis — jest.mock is hoisted, so define mock fns inside the factory
const mockOn = jest.fn();
const mockSubscribe = jest.fn().mockResolvedValue(undefined);
const mockUnsubscribe = jest.fn().mockResolvedValue(undefined);
const mockQuit = jest.fn().mockResolvedValue(undefined);

jest.mock('ioredis', () => {
  const MockRedis = function (this: any) {
    this.on = mockOn;
    this.subscribe = mockSubscribe;
    this.unsubscribe = mockUnsubscribe;
    this.quit = mockQuit;
  };
  return { __esModule: true, default: MockRedis };
});

describe('CandleSubscriberService', () => {
  let service: CandleSubscriberService;
  let mockMarketDataService: Partial<MarketDataService>;
  let mockTradingGateway: Partial<TradingGateway>;
  let messageHandler: (channel: string, message: string) => void;

  const validCandleJson = JSON.stringify({
    instrument: 'US30',
    timeframe: '1m',
    open: 39150.5,
    high: 39155.0,
    low: 39148.0,
    close: 39152.3,
    volume: 120,
    timestamp: '2024-01-15T14:30:00.000Z',
  });

  beforeEach(() => {
    jest.clearAllMocks();

    mockMarketDataService = {
      upsertCandle: jest.fn().mockResolvedValue(undefined),
    };

    mockTradingGateway = {
      emitCandleUpdate: jest.fn(),
    };

    service = new CandleSubscriberService(
      mockMarketDataService as MarketDataService,
      mockTradingGateway as TradingGateway,
    );

    // Capture the message handler registered via .on('message', ...)
    mockOn.mockImplementation((event: string, cb: Function) => {
      if (event === 'message') {
        messageHandler = cb as (channel: string, message: string) => void;
      }
    });
  });

  describe('onModuleInit', () => {
    it('should subscribe to candles:updates channel', async () => {
      await service.onModuleInit();

      expect(mockSubscribe).toHaveBeenCalledWith('candles:updates');
    });

    it('should register a message handler', async () => {
      await service.onModuleInit();

      expect(mockOn).toHaveBeenCalledWith('message', expect.any(Function));
      expect(messageHandler).toBeDefined();
    });

    it('should register an error handler', async () => {
      await service.onModuleInit();

      expect(mockOn).toHaveBeenCalledWith('error', expect.any(Function));
    });
  });

  describe('onModuleDestroy', () => {
    it('should unsubscribe and quit the Redis connection', async () => {
      await service.onModuleInit();
      await service.onModuleDestroy();

      expect(mockUnsubscribe).toHaveBeenCalledWith('candles:updates');
      expect(mockQuit).toHaveBeenCalled();
    });

    it('should handle destroy when not initialized', async () => {
      await expect(service.onModuleDestroy()).resolves.not.toThrow();
    });
  });

  describe('message handling', () => {
    beforeEach(async () => {
      await service.onModuleInit();
    });

    it('should upsert the 1m candle plus one aggregate per higher timeframe', () => {
      messageHandler('candles:updates', validCandleJson);

      // 1m persist + [5m, 15m, 30m, 1h, 4h, 1d] aggregation buckets
      const calls = (mockMarketDataService.upsertCandle as jest.Mock).mock.calls;
      expect(calls).toHaveLength(7);
      expect(calls.map(([c]) => c.timeframe)).toEqual([
        '1m', '5m', '15m', '30m', '1h', '4h', '1d',
      ]);

      // The 1m candle goes through as-is, not yet completed, no force flag
      expect(mockMarketDataService.upsertCandle).toHaveBeenCalledWith(
        {
          instrument: 'US30',
          timeframe: '1m',
          open: 39150.5,
          high: 39155.0,
          low: 39148.0,
          close: 39152.3,
          volume: 120,
          timestamp: new Date('2024-01-15T14:30:00.000Z'),
          completed: false,
        },
        false,
      );

      expect(mockTradingGateway.emitCandleUpdate).toHaveBeenCalledWith(
        'US30',
        '1m',
        expect.objectContaining({
          instrument: 'US30',
          timeframe: '1m',
          close: 39152.3,
        }),
      );
    });

    it('should skip malformed JSON without crashing', () => {
      expect(() => messageHandler('candles:updates', 'not-json')).not.toThrow();

      expect(mockMarketDataService.upsertCandle).not.toHaveBeenCalled();
      expect(mockTradingGateway.emitCandleUpdate).not.toHaveBeenCalled();
    });

    it('should skip messages from other channels', () => {
      messageHandler('other:channel', validCandleJson);

      expect(mockMarketDataService.upsertCandle).not.toHaveBeenCalled();
      expect(mockTradingGateway.emitCandleUpdate).not.toHaveBeenCalled();
    });

    it('should skip candle with missing instrument', () => {
      const invalid = JSON.stringify({
        timeframe: '1m',
        open: 100, high: 101, low: 99, close: 100.5,
        volume: 10, timestamp: '2024-01-15T14:30:00.000Z',
      });

      messageHandler('candles:updates', invalid);

      expect(mockMarketDataService.upsertCandle).not.toHaveBeenCalled();
    });

    it('should ignore the timeframe field — the source stream is always 1m', () => {
      // The subscriber treats every message as a 1m candle and derives
      // higher timeframes itself, so a bogus timeframe is not a rejection.
      const oddTimeframe = JSON.stringify({
        instrument: 'US30', timeframe: '2m',
        open: 100, high: 101, low: 99, close: 100.5,
        volume: 10, timestamp: '2024-01-15T14:30:00.000Z',
      });

      messageHandler('candles:updates', oddTimeframe);

      const calls = (mockMarketDataService.upsertCandle as jest.Mock).mock.calls;
      expect(calls[0][0].timeframe).toBe('1m');
    });

    it('should skip candle with non-numeric OHLCV fields', () => {
      const invalid = JSON.stringify({
        instrument: 'US30', timeframe: '1m',
        open: 'abc', high: 101, low: 99, close: 100.5,
        volume: 10, timestamp: '2024-01-15T14:30:00.000Z',
      });

      messageHandler('candles:updates', invalid);

      expect(mockMarketDataService.upsertCandle).not.toHaveBeenCalled();
    });

    it('should skip candle with invalid timestamp', () => {
      const invalid = JSON.stringify({
        instrument: 'US30', timeframe: '1m',
        open: 100, high: 101, low: 99, close: 100.5,
        volume: 10, timestamp: 'not-a-date',
      });

      messageHandler('candles:updates', invalid);

      expect(mockMarketDataService.upsertCandle).not.toHaveBeenCalled();
    });

    it('should not crash when upsertCandle rejects', async () => {
      (mockMarketDataService.upsertCandle as jest.Mock).mockRejectedValue(
        new Error('DB error'),
      );

      expect(() => messageHandler('candles:updates', validCandleJson)).not.toThrow();
    });
  });
});
