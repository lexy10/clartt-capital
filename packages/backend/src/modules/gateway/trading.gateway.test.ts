import { TradingGateway } from './trading.gateway';
import { Server, Socket } from 'socket.io';
import { Candle, Signal, TradeExecutionResult, Timeframe } from '../../common/types';

describe('TradingGateway', () => {
  let gateway: TradingGateway;
  let mockServer: { to: jest.Mock };
  let mockSocket: { id: string; join: jest.Mock };
  let mockEmit: jest.Mock;

  beforeEach(() => {
    gateway = new TradingGateway();
    mockEmit = jest.fn();
    mockServer = { to: jest.fn().mockReturnValue({ emit: mockEmit }) };
    mockSocket = { id: 'test-client', join: jest.fn() };
    gateway.server = mockServer as unknown as Server;
  });

  describe('subscriptions', () => {
    it('should join candles room with instrument and timeframe', () => {
      gateway.handleSubscribeCandles(
        mockSocket as unknown as Socket,
        { instrument: 'US30', timeframe: '1m' },
      );
      expect(mockSocket.join).toHaveBeenCalledWith('candles:US30:1m');
    });

    it('should join signals room', () => {
      gateway.handleSubscribeSignals(mockSocket as unknown as Socket);
      expect(mockSocket.join).toHaveBeenCalledWith('signals');
    });

    it('should join trades room with accountId', () => {
      gateway.handleSubscribeTrades(
        mockSocket as unknown as Socket,
        { accountId: 'acc-123' },
      );
      expect(mockSocket.join).toHaveBeenCalledWith('trades:acc-123');
    });

    it('should join alerts room', () => {
      gateway.handleSubscribeAlerts(mockSocket as unknown as Socket);
      expect(mockSocket.join).toHaveBeenCalledWith('alerts');
    });

    it('should join kill_switch room', () => {
      gateway.handleSubscribeKillSwitch(mockSocket as unknown as Socket);
      expect(mockSocket.join).toHaveBeenCalledWith('kill_switch');
    });

    it('should join autopilot room scoped to userId', () => {
      gateway.handleSubscribeAutopilot(
        mockSocket as unknown as Socket,
        { userId: 'user-456' },
      );
      expect(mockSocket.join).toHaveBeenCalledWith('autopilot:user-456');
    });

    it('should join backtest room', () => {
      gateway.handleSubscribeBacktest(mockSocket as unknown as Socket);
      expect(mockSocket.join).toHaveBeenCalledWith('backtest');
    });
  });

  describe('emitters', () => {
    it('should emit candle update to correct room', () => {
      const candle: Candle = {
        instrument: 'US30',
        timeframe: '5m',
        open: 34000,
        high: 34050,
        low: 33990,
        close: 34020,
        volume: 1500,
        timestamp: '2024-01-01T00:00:00Z',
      };
      gateway.emitCandleUpdate('US30', '5m', candle);
      expect(mockServer.to).toHaveBeenCalledWith('candles:US30:5m');
      expect(mockEmit).toHaveBeenCalledWith('candleUpdate', candle);
    });

    it('should emit signal to signals room', () => {
      const signal: Signal = {
        id: 'sig-1',
        instrument: 'US30',
        direction: 'BUY',
        entry_price: 34000,
        stop_loss: 33950,
        take_profit: 34100,
        position_size: 1.0,
        confidence_score: 0.85,
        timeframe: '15m',
        order_block_id: 'ob-1',
        strategy_id: 'strat-1',
        mode: 'live',
        metadata: {
          bos_type: 'bullish',
          liquidity_swept: true,
          session: 'london',
          spread_at_generation: 2.5,
          volatility_ratio: 1.1,
        },
        created_at: '2024-01-01T00:00:00Z',
      };
      gateway.emitSignal(signal);
      expect(mockServer.to).toHaveBeenCalledWith('signals');
      expect(mockEmit).toHaveBeenCalledWith('signal', signal);
    });

    it('should emit trade execution to account-specific room', () => {
      const trade: TradeExecutionResult = {
        id: 'trade-1',
        signal_id: 'sig-1',
        account_id: 'acc-123',
        order_id: 12345,
        fill_price: 34001,
        execution_latency_ms: 45,
        status: 'filled',
        slippage: 1.0,
        spread_at_execution: 2.3,
        created_at: '2024-01-01T00:00:00Z',
      };
      gateway.emitTradeExecution('acc-123', trade);
      expect(mockServer.to).toHaveBeenCalledWith('trades:acc-123');
      expect(mockEmit).toHaveBeenCalledWith('tradeExecution', trade);
    });

    it('should emit alert triggered to alerts room', () => {
      const alert = {
        id: 'alert-1',
        instrument: 'US30',
        condition_type: 'price_above',
        condition_value: { price: 34100 },
        triggered_at: '2024-01-01T00:05:00Z',
      };
      gateway.emitAlertTriggered(alert);
      expect(mockServer.to).toHaveBeenCalledWith('alerts');
      expect(mockEmit).toHaveBeenCalledWith('alertTriggered', alert);
    });

    it('should emit kill switch status to kill_switch room', () => {
      const status = { is_active: true, activated_at: '2024-01-01T00:10:00Z' };
      gateway.emitKillSwitchStatus(status);
      expect(mockServer.to).toHaveBeenCalledWith('kill_switch');
      expect(mockEmit).toHaveBeenCalledWith('killSwitchStatus', status);
    });

    it('should emit autopilot state change to user-scoped room', () => {
      const payload = { accountId: 'acc-123', enabled: true, updatedAt: '2024-01-01T00:00:00Z' };
      gateway.emitAutopilotStateChange('user-456', payload);
      expect(mockServer.to).toHaveBeenCalledWith('autopilot:user-456');
      expect(mockEmit).toHaveBeenCalledWith('autopilot:state_change', payload);
    });

    it('should emit strategy overlay to user-scoped room', () => {
      const payload = {
        accountId: 'acc-123',
        overlays: [{
          kind: 'entry_zone' as const,
          priceHigh: 34100,
          priceLow: 34050,
          startTime: '2024-01-01T00:00:00Z',
          direction: 'bullish' as const,
        }],
      };
      gateway.emitStrategyOverlay('user-456', payload);
      expect(mockServer.to).toHaveBeenCalledWith('autopilot:user-456');
      expect(mockEmit).toHaveBeenCalledWith('autopilot:strategy_overlay', payload);
    });

    it('should emit trade entry to user-scoped room', () => {
      const payload = {
        accountId: 'acc-123',
        trade: {
          id: 'trade-1',
          signalId: 'sig-1',
          direction: 'BUY' as const,
          entryPrice: 34000,
          stopLoss: 33950,
          takeProfit: 34100,
          positionSize: 1.0,
          executedAt: '2024-01-01T00:00:00Z',
        },
      };
      gateway.emitTradeEntry('user-456', payload);
      expect(mockServer.to).toHaveBeenCalledWith('autopilot:user-456');
      expect(mockEmit).toHaveBeenCalledWith('autopilot:trade_entry', payload);
    });

    it('should emit trade exit to user-scoped room', () => {
      const payload = {
        accountId: 'acc-123',
        trade: {
          id: 'trade-1',
          signalId: 'sig-1',
          direction: 'BUY' as const,
          entryPrice: 34000,
          exitPrice: 34080,
          profitLoss: 80,
          exitReason: 'take_profit' as const,
          executedAt: '2024-01-01T00:05:00Z',
        },
      };
      gateway.emitTradeExit('user-456', payload);
      expect(mockServer.to).toHaveBeenCalledWith('autopilot:user-456');
      expect(mockEmit).toHaveBeenCalledWith('autopilot:trade_exit', payload);
    });

    it('should emit backtest update to backtest room', () => {
      const payload = {
        result_id: 'result-1',
        strategy_id: 'strat-1',
        status: 'completed' as const,
        win_rate: 0.6,
        max_drawdown: 1200,
        sharpe_ratio: 1.8,
        profit_factor: 2.1,
        expectancy: 45,
        total_trades: 42,
        winning_trades: 25,
        losing_trades: 17,
        gross_profit: 5200,
        gross_loss: -2476,
        net_profit: 2724,
      };
      gateway.emitBacktestUpdate(payload);
      expect(mockServer.to).toHaveBeenCalledWith('backtest');
      expect(mockEmit).toHaveBeenCalledWith('backtest:update', payload);
    });
  });
});
