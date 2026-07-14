import { MarketDataController } from './market-data.controller';
import { MarketDataService } from './market-data.service';
import { Candle } from '../../common/types';

describe('MarketDataController', () => {
  let controller: MarketDataController;
  let service: Partial<MarketDataService>;

  beforeEach(() => {
    service = {
      getCandles: jest.fn().mockResolvedValue([]),
      getInstruments: jest.fn().mockResolvedValue([
        { symbol: 'US30', displayName: 'Dow Jones Industrial Average', type: 'index' },
      ]),
    };
    const backfillService = {
      triggerBackfill: jest.fn().mockResolvedValue(undefined),
    };
    controller = new MarketDataController(service as MarketDataService, backfillService as never);
  });

  describe('GET /market-data/candles', () => {
    it('should call service with parsed query params', async () => {
      await controller.getCandles({
        instrument: 'US30',
        timeframe: '1m',
        count: '50',
      });

      expect(service.getCandles).toHaveBeenCalledWith('US30', '1m', 50);
    });

    it('should default count to 100 when not provided', async () => {
      await controller.getCandles({
        instrument: 'US30',
        timeframe: '5m',
      });

      expect(service.getCandles).toHaveBeenCalledWith('US30', '5m', 100);
    });

    it('should return candles from service', async () => {
      const candles: Candle[] = [
        {
          instrument: 'US30',
          timeframe: '1m',
          open: 34000,
          high: 34050,
          low: 33990,
          close: 34020,
          volume: 100,
          timestamp: '2024-01-01T10:00:00Z',
        },
      ];
      (service.getCandles as jest.Mock).mockResolvedValue(candles);

      const result = await controller.getCandles({
        instrument: 'US30',
        timeframe: '1m',
      });

      expect(result).toEqual(candles);
    });
  });

  describe('GET /market-data/instruments', () => {
    it('should return instruments from service', async () => {
      const result = await controller.getInstruments();
      expect(result).toEqual([
        { symbol: 'US30', displayName: 'Dow Jones Industrial Average', type: 'index' },
      ]);
    });
  });
});
