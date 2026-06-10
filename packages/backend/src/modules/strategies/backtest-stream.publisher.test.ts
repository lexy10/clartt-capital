import { HttpException, HttpStatus } from '@nestjs/common';
import { BacktestStreamPublisher, BacktestRequestMessage } from './backtest-stream.publisher';

describe('BacktestStreamPublisher', () => {
  let publisher: BacktestStreamPublisher;
  let redisMock: Record<string, jest.Mock>;

  const mockMessage: BacktestRequestMessage = {
    result_id: 'result-1',
    strategy_id: 'strategy-1',
    strategy_config: { name: 'test' },
    instrument: 'US30',
    timeframe: '1h',
    params: {},
    start_date: '2024-01-01T00:00:00Z',
    end_date: '2024-06-01T00:00:00Z',
  };

  beforeEach(() => {
    delete process.env.BACKTEST_REQUEST_HIGH_WATER_MARK;
    redisMock = {
      xinfo: jest.fn(),
      xadd: jest.fn().mockResolvedValue('1234567890-0'),
    };
    publisher = new BacktestStreamPublisher(redisMock as any);
  });

  describe('getConsumerLag', () => {
    it('should return pending count from strategy-engine consumer group', async () => {
      redisMock.xinfo.mockResolvedValue([
        ['name', 'strategy-engine', 'consumers', 1, 'pending', 0, 'last-delivered-id', '0-0', 'pel-count', 7],
      ]);

      const lag = await publisher.getConsumerLag();
      expect(lag).toBe(7);
      expect(redisMock.xinfo).toHaveBeenCalledWith('GROUPS', 'backtest:requests');
    });

    it('should return 0 when consumer group does not exist', async () => {
      redisMock.xinfo.mockResolvedValue([
        ['name', 'other-group', 'consumers', 1, 'pending', 0, 'last-delivered-id', '0-0', 'pel-count', 5],
      ]);

      const lag = await publisher.getConsumerLag();
      expect(lag).toBe(0);
    });

    it('should return 0 when XINFO fails (fail-open)', async () => {
      redisMock.xinfo.mockRejectedValue(new Error('ERR no such key'));

      const lag = await publisher.getConsumerLag();
      expect(lag).toBe(0);
    });

    it('should return 0 when no groups exist', async () => {
      redisMock.xinfo.mockResolvedValue([]);

      const lag = await publisher.getConsumerLag();
      expect(lag).toBe(0);
    });
  });

  describe('publishRequest', () => {
    it('should publish when lag is within high-water mark', async () => {
      redisMock.xinfo.mockResolvedValue([
        ['name', 'strategy-engine', 'consumers', 1, 'pending', 0, 'last-delivered-id', '0-0', 'pel-count', 5],
      ]);

      const id = await publisher.publishRequest(mockMessage);
      expect(id).toBe('1234567890-0');
      expect(redisMock.xadd).toHaveBeenCalledWith(
        'backtest:requests',
        '*',
        'data',
        JSON.stringify(mockMessage),
      );
    });

    it('should reject with 429 when lag exceeds high-water mark', async () => {
      redisMock.xinfo.mockResolvedValue([
        ['name', 'strategy-engine', 'consumers', 1, 'pending', 0, 'last-delivered-id', '0-0', 'pel-count', 15],
      ]);

      await expect(publisher.publishRequest(mockMessage)).rejects.toThrow(HttpException);
      try {
        await publisher.publishRequest(mockMessage);
      } catch (e) {
        expect((e as HttpException).getStatus()).toBe(HttpStatus.TOO_MANY_REQUESTS);
        expect((e as HttpException).message).toBe('Backtest queue is full');
      }
      expect(redisMock.xadd).not.toHaveBeenCalled();
    });

    it('should allow publish when lag equals high-water mark', async () => {
      redisMock.xinfo.mockResolvedValue([
        ['name', 'strategy-engine', 'consumers', 1, 'pending', 0, 'last-delivered-id', '0-0', 'pel-count', 10],
      ]);

      const id = await publisher.publishRequest(mockMessage);
      expect(id).toBe('1234567890-0');
    });

    it('should allow publish when XINFO fails (fail-open)', async () => {
      redisMock.xinfo.mockRejectedValue(new Error('Connection lost'));

      const id = await publisher.publishRequest(mockMessage);
      expect(id).toBe('1234567890-0');
    });

    it('should use custom high-water mark from env var', () => {
      process.env.BACKTEST_REQUEST_HIGH_WATER_MARK = '25';
      const customPublisher = new BacktestStreamPublisher(redisMock as any);
      expect((customPublisher as any).highWaterMark).toBe(25);
    });

    it('should use default high-water mark of 10 when env var is not set', () => {
      expect((publisher as any).highWaterMark).toBe(10);
    });

    it('should throw when XADD returns null', async () => {
      redisMock.xinfo.mockResolvedValue([]);
      redisMock.xadd.mockResolvedValue(null);

      await expect(publisher.publishRequest(mockMessage)).rejects.toThrow(
        'Failed to publish backtest request to Redis stream',
      );
    });
  });
});
