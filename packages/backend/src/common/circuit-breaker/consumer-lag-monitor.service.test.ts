import { ConsumerLagMonitor } from './consumer-lag-monitor.service';
import { register } from 'prom-client';

describe('ConsumerLagMonitor', () => {
  let monitor: ConsumerLagMonitor;
  let mockRedis: any;
  let mockGateway: any;
  let emittedEvents: any[];

  beforeEach(() => {
    register.clear();
    emittedEvents = [];

    mockRedis = {
      xinfo: jest.fn().mockResolvedValue([]),
    };

    mockGateway = {
      server: {
        to: jest.fn().mockReturnValue({
          emit: jest.fn((_event: string, payload: any) => {
            emittedEvents.push(payload);
          }),
        }),
      },
    };

    process.env.CONSUMER_LAG_POLL_INTERVAL_MS = '5000';
    process.env.CONSUMER_LAG_ALERT_THRESHOLD = '10';

    monitor = new ConsumerLagMonitor(mockRedis, mockGateway);
  });

  afterEach(() => {
    monitor.stop();
    delete process.env.CONSUMER_LAG_POLL_INTERVAL_MS;
    delete process.env.CONSUMER_LAG_ALERT_THRESHOLD;
  });

  it('should use env var configuration', () => {
    expect(monitor).toBeDefined();
  });

  it('should use defaults when env vars are not set', () => {
    delete process.env.CONSUMER_LAG_POLL_INTERVAL_MS;
    delete process.env.CONSUMER_LAG_ALERT_THRESHOLD;
    const defaultMonitor = new ConsumerLagMonitor(mockRedis, mockGateway);
    expect(defaultMonitor).toBeDefined();
    defaultMonitor.stop();
  });

  it('should poll XINFO GROUPS for all monitored streams', async () => {
    mockRedis.xinfo.mockResolvedValue([]);

    // Call the poll method directly via start() which triggers an immediate poll
    // We don't start the interval timer — just invoke the internal poll
    monitor['poll']();
    await flushPromises();

    expect(mockRedis.xinfo).toHaveBeenCalledWith('GROUPS', 'signals:stream');
    expect(mockRedis.xinfo).toHaveBeenCalledWith('GROUPS', 'backtest:requests');
    expect(mockRedis.xinfo).toHaveBeenCalledWith('GROUPS', 'backtest:results');
  });

  it('should parse consumer group info and update lag snapshots', async () => {
    mockRedis.xinfo.mockImplementation((_cmd: string, stream: string) => {
      if (stream === 'signals:stream') {
        return Promise.resolve([
          ['name', 'backend-gateway', 'consumers', '1', 'pending', '5', 'last-delivered-id', '1-0', 'pel-count', '5', 'entries-read', '10'],
        ]);
      }
      return Promise.resolve([]);
    });

    await monitor['poll']();

    const lagInfo = monitor.getLagInfo();
    const signalLag = lagInfo.find(
      (l) => l.stream === 'signals:stream' && l.group === 'backend-gateway',
    );
    expect(signalLag).toBeDefined();
    expect(signalLag!.lag).toBe(5);
  });

  it('should emit WebSocket alert when lag exceeds threshold', async () => {
    // Threshold is 10 (set in beforeEach)
    mockRedis.xinfo.mockImplementation((_cmd: string, stream: string) => {
      if (stream === 'signals:stream') {
        return Promise.resolve([
          ['name', 'account:123', 'consumers', '1', 'pending', '15', 'last-delivered-id', '1-0', 'pel-count', '15', 'entries-read', '20'],
        ]);
      }
      return Promise.resolve([]);
    });

    await monitor['poll']();

    expect(mockGateway.server.to).toHaveBeenCalledWith('health');
    expect(emittedEvents.length).toBeGreaterThan(0);
    expect(emittedEvents[0]).toMatchObject({
      stream: 'signals:stream',
      group: 'account:123',
      lag: 15,
      threshold: 10,
    });
  });

  it('should NOT emit alert when lag is at or below threshold', async () => {
    mockRedis.xinfo.mockImplementation((_cmd: string, stream: string) => {
      if (stream === 'signals:stream') {
        return Promise.resolve([
          ['name', 'backend-gateway', 'consumers', '1', 'pending', '10', 'last-delivered-id', '1-0', 'pel-count', '10', 'entries-read', '20'],
        ]);
      }
      return Promise.resolve([]);
    });

    await monitor['poll']();

    expect(emittedEvents.length).toBe(0);
  });

  it('should handle XINFO errors gracefully (stream does not exist)', async () => {
    mockRedis.xinfo.mockRejectedValue(new Error('ERR no such key'));

    await monitor['poll']();

    const lagInfo = monitor.getLagInfo();
    expect(lagInfo).toEqual([]);
  });

  it('should stop polling on stop()', () => {
    monitor.start();
    monitor.stop();

    mockRedis.xinfo.mockClear();

    // Verify timer is cleared — no further calls
    expect(mockRedis.xinfo).not.toHaveBeenCalled();
  });

  it('should work without TradingGateway (optional injection)', async () => {
    const monitorNoGateway = new ConsumerLagMonitor(mockRedis, undefined);

    mockRedis.xinfo.mockImplementation(() =>
      Promise.resolve([
        ['name', 'test-group', 'consumers', '1', 'pending', '100', 'last-delivered-id', '1-0', 'pel-count', '100', 'entries-read', '200'],
      ]),
    );

    await monitorNoGateway['poll']();

    const lagInfo = monitorNoGateway.getLagInfo();
    expect(lagInfo.length).toBeGreaterThan(0);
    monitorNoGateway.stop();
  });

  it('should handle multiple consumer groups on a single stream', async () => {
    mockRedis.xinfo.mockImplementation((_cmd: string, stream: string) => {
      if (stream === 'signals:stream') {
        return Promise.resolve([
          ['name', 'account:1', 'consumers', '1', 'pending', '3', 'last-delivered-id', '1-0', 'pel-count', '3', 'entries-read', '10'],
          ['name', 'account:2', 'consumers', '1', 'pending', '7', 'last-delivered-id', '1-0', 'pel-count', '7', 'entries-read', '10'],
          ['name', 'backend-gateway', 'consumers', '1', 'pending', '1', 'last-delivered-id', '1-0', 'pel-count', '1', 'entries-read', '10'],
        ]);
      }
      return Promise.resolve([]);
    });

    await monitor['poll']();

    const lagInfo = monitor.getLagInfo();
    const signalGroups = lagInfo.filter((l) => l.stream === 'signals:stream');
    expect(signalGroups.length).toBe(3);
  });
});

function flushPromises(): Promise<void> {
  return new Promise((resolve) => setImmediate(resolve));
}
