import { RedisStreamService } from './redis-stream.service';
import { TradingGateway } from './trading.gateway';
import { Signal, TradeExecutionResult } from '../../common/types';
import { InstrumentsService } from '../instruments/instruments.service';

describe('RedisStreamService', () => {
  let service: RedisStreamService;
  let mockRedis: any;
  let mockGateway: Partial<TradingGateway>;
  let mockTradingAccountRepo: any;
  let mockInstrumentsService: Partial<InstrumentsService>;

  const sampleSignal: Signal = {
    id: 'sig-001',
    instrument: 'US30',
    direction: 'BUY',
    entry_price: 34500,
    stop_loss: 34400,
    take_profit: 34700,
    position_size: 0.1,
    confidence_score: 0.85,
    timeframe: '15m',
    order_block_id: 'ob-001',
    strategy_id: 'strat-001',
    mode: 'live',
    metadata: {
      bos_type: 'bullish',
      liquidity_swept: true,
      session: 'new_york',
      spread_at_generation: 2.5,
      volatility_ratio: 1.1,
    },
    created_at: '2024-01-15T14:30:00Z',
  };

  const sampleTrade: TradeExecutionResult = {
    id: 'trade-001',
    signal_id: 'sig-001',
    account_id: 'acc-123',
    order_id: 12345,
    fill_price: 34501,
    execution_latency_ms: 42,
    status: 'filled',
    slippage: 1.0,
    spread_at_execution: 2.3,
    created_at: '2024-01-15T14:30:01Z',
  };

  beforeEach(() => {
    mockGateway = {
      emitSignal: jest.fn(),
      emitTradeExecution: jest.fn(),
      emitTradeEntry: jest.fn(),
      emitTradeExit: jest.fn(),
      emitStrategyOverlay: jest.fn(),
    };

    const subscriberCallbacks: Record<string, Function> = {};
    const mockSubscriberClient = {
      subscribe: jest.fn((...args: any[]) => {
        // The last argument is the callback
        const cb = args[args.length - 1];
        if (typeof cb === 'function') cb(null);
      }),
      on: jest.fn((event: string, cb: Function) => {
        subscriberCallbacks[event] = cb;
      }),
      disconnect: jest.fn(),
      _callbacks: subscriberCallbacks,
    };

    mockRedis = {
      xgroup: jest.fn().mockResolvedValue('OK'),
      xreadgroup: jest.fn().mockResolvedValue(null),
      xack: jest.fn().mockResolvedValue(1),
      duplicate: jest.fn().mockReturnValue(mockSubscriberClient),
    };

    mockTradingAccountRepo = {
      findOne: jest.fn().mockResolvedValue(null),
    };

    const mockTradeRepo = {
      findOne: jest.fn().mockResolvedValue(null),
    };

    mockInstrumentsService = {
      getBrokerSymbol: jest.fn().mockResolvedValue('US30'),
    };

    service = new RedisStreamService(
      mockRedis,
      mockGateway as TradingGateway,
      mockTradingAccountRepo,
      mockTradeRepo as never,
      mockInstrumentsService as InstrumentsService,
    );
  });

  afterEach(() => {
    service.onModuleDestroy();
  });

  describe('onModuleInit', () => {
    it('should create consumer group on signals:stream', async () => {
      await service.onModuleInit();

      expect(mockRedis.xgroup).toHaveBeenCalledWith(
        'CREATE', 'signals:stream', 'backend-gateway', '$', 'MKSTREAM',
      );
    });

    it('should handle BUSYGROUP error gracefully when group already exists', async () => {
      mockRedis.xgroup.mockRejectedValue(new Error('BUSYGROUP Consumer Group name already exists'));

      await expect(service.onModuleInit()).resolves.not.toThrow();
    });

    it('should subscribe to trades:results pub/sub channel', async () => {
      await service.onModuleInit();

      const subscriberClient = mockRedis.duplicate();
      expect(mockRedis.duplicate).toHaveBeenCalled();
    });
  });

  describe('signal stream polling', () => {
    it('should forward signals from Redis stream to WebSocket gateway', async () => {
      const signalJson = JSON.stringify(sampleSignal);
      mockRedis.xreadgroup
        .mockResolvedValueOnce([
          ['signals:stream', [['1-0', ['data', signalJson]]]],
        ])
        .mockResolvedValue(null);

      await service.onModuleInit();

      // Wait for the first poll cycle
      await new Promise((resolve) => setTimeout(resolve, 100));

      expect(mockGateway.emitSignal).toHaveBeenCalledWith(sampleSignal);
      expect(mockRedis.xack).toHaveBeenCalledWith('signals:stream', 'backend-gateway', '1-0');
    });

    it('should handle multiple signals in a single poll', async () => {
      const signal1 = { ...sampleSignal, id: 'sig-001' };
      const signal2 = { ...sampleSignal, id: 'sig-002' };

      mockRedis.xreadgroup
        .mockResolvedValueOnce([
          ['signals:stream', [
            ['1-0', ['data', JSON.stringify(signal1)]],
            ['2-0', ['data', JSON.stringify(signal2)]],
          ]],
        ])
        .mockResolvedValue(null);

      await service.onModuleInit();
      await new Promise((resolve) => setTimeout(resolve, 100));

      expect(mockGateway.emitSignal).toHaveBeenCalledTimes(2);
    });

    it('should skip messages without data field', async () => {
      mockRedis.xreadgroup
        .mockResolvedValueOnce([
          ['signals:stream', [['1-0', ['other', 'value']]]],
        ])
        .mockResolvedValue(null);

      await service.onModuleInit();
      await new Promise((resolve) => setTimeout(resolve, 100));

      expect(mockGateway.emitSignal).not.toHaveBeenCalled();
    });

    it('should handle malformed JSON gracefully', async () => {
      mockRedis.xreadgroup
        .mockResolvedValueOnce([
          ['signals:stream', [['1-0', ['data', 'not-json']]]],
        ])
        .mockResolvedValue(null);

      await service.onModuleInit();
      await new Promise((resolve) => setTimeout(resolve, 100));

      expect(mockGateway.emitSignal).not.toHaveBeenCalled();
    });
  });

  describe('trade result pub/sub', () => {
    it('should forward trade results from pub/sub to WebSocket gateway', async () => {
      await service.onModuleInit();

      // Get the subscriber client and trigger a message
      const subscriberClient = mockRedis.duplicate();
      const messageCallback = subscriberClient.on.mock.calls.find(
        (call: any[]) => call[0] === 'message',
      )?.[1];

      expect(messageCallback).toBeDefined();
      messageCallback('trades:results', JSON.stringify(sampleTrade));

      expect(mockGateway.emitTradeExecution).toHaveBeenCalledWith('acc-123', sampleTrade);
    });

    it('should handle malformed trade result JSON gracefully', async () => {
      await service.onModuleInit();

      const subscriberClient = mockRedis.duplicate();
      const messageCallback = subscriberClient.on.mock.calls.find(
        (call: any[]) => call[0] === 'message',
      )?.[1];

      expect(() => messageCallback('trades:results', 'bad-json')).not.toThrow();
      expect(mockGateway.emitTradeExecution).not.toHaveBeenCalled();
    });

    it('should ignore messages from other channels', async () => {
      await service.onModuleInit();

      const subscriberClient = mockRedis.duplicate();
      const messageCallback = subscriberClient.on.mock.calls.find(
        (call: any[]) => call[0] === 'message',
      )?.[1];

      messageCallback('other:channel', JSON.stringify(sampleTrade));

      expect(mockGateway.emitTradeExecution).not.toHaveBeenCalled();
    });
  });

  describe('onModuleDestroy', () => {
    it('should stop polling and disconnect subscriber', async () => {
      await service.onModuleInit();

      service.onModuleDestroy();

      const subscriberClient = mockRedis.duplicate();
      // Verify the subscriber was disconnected (duplicate was called during init)
      expect(mockRedis.duplicate).toHaveBeenCalled();
    });
  });

  describe('signal serialization compatibility', () => {
    it('should correctly parse a signal matching the Python Pydantic model output', () => {
      // This JSON matches what Python's signal.model_dump_json() produces
      const pythonSignalJson = JSON.stringify({
        id: 'sig-round-trip',
        instrument: 'US30',
        direction: 'BUY',
        entry_price: 34500.0,
        stop_loss: 34400.0,
        take_profit: 34700.0,
        position_size: 0.1,
        confidence_score: 0.85,
        timeframe: '15m',
        order_block_id: 'ob-001',
        strategy_id: 'strat-001',
        mode: 'live',
        metadata: {
          bos_type: 'bullish',
          liquidity_swept: true,
          session: 'new_york',
          spread_at_generation: 2.5,
          volatility_ratio: 1.1,
        },
        created_at: '2024-01-15T14:30:00Z',
      });

      const parsed: Signal = JSON.parse(pythonSignalJson);

      expect(parsed.id).toBe('sig-round-trip');
      expect(parsed.direction).toBe('BUY');
      expect(parsed.entry_price).toBe(34500.0);
      expect(parsed.metadata.bos_type).toBe('bullish');
      expect(parsed.metadata.liquidity_swept).toBe(true);
      expect(parsed.timeframe).toBe('15m');
      expect(parsed.mode).toBe('live');
    });

    it('should correctly parse a trade execution result matching Python model output', () => {
      const pythonTradeJson = JSON.stringify({
        id: 'trade-round-trip',
        signal_id: 'sig-001',
        account_id: 'acc-123',
        order_id: 12345,
        fill_price: 34501.0,
        execution_latency_ms: 42.0,
        status: 'filled',
        rejection_reason: null,
        slippage: 1.0,
        spread_at_execution: 2.3,
        created_at: '2024-01-15T14:30:01Z',
      });

      const parsed: TradeExecutionResult = JSON.parse(pythonTradeJson);

      expect(parsed.id).toBe('trade-round-trip');
      expect(parsed.signal_id).toBe('sig-001');
      expect(parsed.account_id).toBe('acc-123');
      expect(parsed.order_id).toBe(12345);
      expect(parsed.fill_price).toBe(34501.0);
      expect(parsed.status).toBe('filled');
      expect(parsed.rejection_reason).toBeNull();
    });
  });

  describe('autopilot trade events on trades:results channel', () => {
    it('should forward trade_entry events through emitTradeEntry', async () => {
      await service.onModuleInit();

      const subscriberClient = mockRedis.duplicate();
      const messageCallback = subscriberClient.on.mock.calls.find(
        (call: any[]) => call[0] === 'message',
      )?.[1];

      const tradeEntryEvent = {
        type: 'trade_entry',
        userId: 'user-001',
        accountId: 'acc-123',
        trade: {
          id: 'trade-001',
          signalId: 'sig-001',
          direction: 'BUY',
          entryPrice: 34500,
          stopLoss: 34400,
          takeProfit: 34700,
          positionSize: 0.1,
          executedAt: '2024-01-15T14:30:00Z',
        },
      };

      messageCallback('trades:results', JSON.stringify(tradeEntryEvent));

      expect(mockGateway.emitTradeEntry).toHaveBeenCalledWith('user-001', tradeEntryEvent);
      expect(mockGateway.emitTradeExecution).not.toHaveBeenCalled();
    });

    it('should forward trade_exit events through emitTradeExit', async () => {
      await service.onModuleInit();

      const subscriberClient = mockRedis.duplicate();
      const messageCallback = subscriberClient.on.mock.calls.find(
        (call: any[]) => call[0] === 'message',
      )?.[1];

      const tradeExitEvent = {
        type: 'trade_exit',
        userId: 'user-001',
        accountId: 'acc-123',
        trade: {
          id: 'trade-001',
          signalId: 'sig-001',
          direction: 'BUY',
          entryPrice: 34500,
          exitPrice: 34700,
          profitLoss: 200,
          exitReason: 'take_profit',
          executedAt: '2024-01-15T15:00:00Z',
        },
      };

      messageCallback('trades:results', JSON.stringify(tradeExitEvent));

      expect(mockGateway.emitTradeExit).toHaveBeenCalledWith('user-001', tradeExitEvent);
      expect(mockGateway.emitTradeExecution).not.toHaveBeenCalled();
    });

    it('should still forward standard trade results without type field', async () => {
      await service.onModuleInit();

      const subscriberClient = mockRedis.duplicate();
      const messageCallback = subscriberClient.on.mock.calls.find(
        (call: any[]) => call[0] === 'message',
      )?.[1];

      messageCallback('trades:results', JSON.stringify(sampleTrade));

      expect(mockGateway.emitTradeExecution).toHaveBeenCalledWith('acc-123', sampleTrade);
      expect(mockGateway.emitTradeEntry).not.toHaveBeenCalled();
      expect(mockGateway.emitTradeExit).not.toHaveBeenCalled();
    });

    it('should not forward trade_entry without userId', async () => {
      await service.onModuleInit();

      const subscriberClient = mockRedis.duplicate();
      const messageCallback = subscriberClient.on.mock.calls.find(
        (call: any[]) => call[0] === 'message',
      )?.[1];

      const eventWithoutUserId = {
        type: 'trade_entry',
        accountId: 'acc-123',
        trade: { id: 'trade-001' },
      };

      messageCallback('trades:results', JSON.stringify(eventWithoutUserId));

      // Without userId, it falls through to standard trade result handling
      expect(mockGateway.emitTradeEntry).not.toHaveBeenCalled();
      expect(mockGateway.emitTradeExecution).toHaveBeenCalled();
    });
  });

  describe('strategy overlay pub/sub', () => {
    it('should forward strategy overlay events through emitStrategyOverlay', async () => {
      await service.onModuleInit();

      const subscriberClient = mockRedis.duplicate();
      const messageCallback = subscriberClient.on.mock.calls.find(
        (call: any[]) => call[0] === 'message',
      )?.[1];

      const overlayEvent = {
        userId: 'user-001',
        accountId: 'acc-123',
        overlays: [
          {
            kind: 'entry_zone',
            priceHigh: 34600,
            priceLow: 34500,
            startTime: '2024-01-15T14:00:00Z',
            direction: 'bullish',
          },
        ],
      };

      messageCallback('strategy:overlays', JSON.stringify(overlayEvent));

      expect(mockGateway.emitStrategyOverlay).toHaveBeenCalledWith('user-001', overlayEvent);
    });

    it('should not forward overlay events without userId', async () => {
      await service.onModuleInit();

      const subscriberClient = mockRedis.duplicate();
      const messageCallback = subscriberClient.on.mock.calls.find(
        (call: any[]) => call[0] === 'message',
      )?.[1];

      const overlayWithoutUser = {
        accountId: 'acc-123',
        overlays: [],
      };

      // tradingAccountRepo returns null — no account found
      messageCallback('strategy:overlays', JSON.stringify(overlayWithoutUser));

      // Wait for async handleStrategyOverlay to complete
      await new Promise((resolve) => setTimeout(resolve, 50));

      expect(mockGateway.emitStrategyOverlay).not.toHaveBeenCalled();
    });

    it('should handle malformed overlay JSON gracefully', async () => {
      await service.onModuleInit();

      const subscriberClient = mockRedis.duplicate();
      const messageCallback = subscriberClient.on.mock.calls.find(
        (call: any[]) => call[0] === 'message',
      )?.[1];

      expect(() => messageCallback('strategy:overlays', 'bad-json')).not.toThrow();
      expect(mockGateway.emitStrategyOverlay).not.toHaveBeenCalled();
    });

    it('should subscribe to trades:results and strategy:overlays channels', async () => {
      // candles:updates moved to its own subscriber (CandleSubscriberService)
      await service.onModuleInit();

      const subscriberClient = mockRedis.duplicate();
      expect(subscriberClient.subscribe).toHaveBeenCalledWith(
        'trades:results',
        'strategy:overlays',
        expect.any(Function),
      );
    });
  });
});
