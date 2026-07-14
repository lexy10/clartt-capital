import {
  WebSocketGateway,
  WebSocketServer,
  SubscribeMessage,
  MessageBody,
  ConnectedSocket,
  OnGatewayConnection,
  OnGatewayDisconnect,
} from '@nestjs/websockets';
import { Logger } from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';
import { Server, Socket } from 'socket.io';
import { Candle, Signal, TradeExecutionResult, Timeframe } from '../../common/types';
import { ReconciliationDiscrepancyPayload } from '../reconciliation/types';

export interface AutopilotStateChangePayload {
  accountId: string;
  enabled: boolean;
  updatedAt: string;
}

export interface MasterAutopilotChangePayload {
  enabled: boolean;
  updatedAt: string;
}

export interface StrategyOverlayPayload {
  accountId: string;
  overlays: Array<{
    kind: 'entry_zone' | 'exit_zone' | 'order_block';
    priceHigh?: number;
    priceLow?: number;
    price?: number;
    type?: 'stop_loss' | 'take_profit';
    startTime: string;
    endTime?: string;
    direction: 'bullish' | 'bearish' | 'neutral';
    signalId?: string;
  }>;
}

export interface TradeEntryPayload {
  accountId: string;
  trade: {
    id: string;
    signalId: string;
    direction: 'BUY' | 'SELL';
    entryPrice: number;
    stopLoss: number;
    takeProfit: number;
    positionSize: number;
    executedAt: string;
  };
}

export interface TradeExitPayload {
  accountId: string;
  trade: {
    id: string;
    signalId: string;
    direction: 'BUY' | 'SELL';
    entryPrice: number;
    exitPrice: number;
    profitLoss: number;
    exitReason: 'stop_loss' | 'take_profit' | 'strategy_exit' | 'manual';
    executedAt: string;
  };
}

export interface AlertTriggered {
  id: string;
  instrument: string;
  condition_type: string;
  condition_value: Record<string, unknown>;
  triggered_at: string;
}

export interface KillSwitchStatus {
  is_active: boolean;
  activated_at?: string;
  deactivated_at?: string;
}

export interface BacktestUpdatePayload {
  result_id: string;
  strategy_id: string;
  status: 'running' | 'completed' | 'failed';
  win_rate?: number;
  max_drawdown?: number;
  sharpe_ratio?: number;
  profit_factor?: number;
  expectancy?: number;
  total_trades?: number;
  winning_trades?: number;
  losing_trades?: number;
  gross_profit?: number;
  gross_loss?: number;
  net_profit?: number;
  average_rr?: number;
  equity_curve?: number[];
  trade_results?: unknown[];
  error_message?: string;
}

@WebSocketGateway({
  // In production the dashboard reaches us same-origin through the nginx
  // proxy, so CORS only matters for direct cross-origin connections —
  // lock it to the configured origin instead of '*'.
  cors: { origin: process.env.CORS_ORIGIN || 'http://localhost:5173' },
})
export class TradingGateway implements OnGatewayConnection, OnGatewayDisconnect {
  private readonly logger = new Logger(TradingGateway.name);

  constructor(private readonly jwtService: JwtService) {}

  @WebSocketServer()
  server: Server;

  handleConnection(client: Socket): void {
    // Every socket must present a valid access token at connection time.
    // Without this gate, anyone who can reach the port can subscribe to
    // live signals, trades, and account P&L. The dashboard sends the token
    // via socket.io's auth payload; a query param fallback is accepted for
    // tooling. Expiry is only checked here (not continuously) — an expired
    // token just means the next reconnect must present a fresh one.
    const token =
      (client.handshake.auth?.token as string | undefined) ??
      (client.handshake.query?.token as string | undefined);
    if (!token) {
      this.logger.warn(`Client ${client.id} rejected: no auth token`);
      client.disconnect(true);
      return;
    }
    try {
      const payload = this.jwtService.verify<{ sub: string }>(token);
      client.data.userId = payload.sub;
      this.logger.log(`Client connected: ${client.id} (user ${payload.sub})`);
    } catch {
      this.logger.warn(`Client ${client.id} rejected: invalid/expired token`);
      client.disconnect(true);
    }
  }

  handleDisconnect(client: Socket): void {
    this.logger.log(`Client disconnected: ${client.id}`);
  }

  // --- Subscription handlers (clients join rooms) ---

  @SubscribeMessage('subscribeCandles')
  handleSubscribeCandles(
    @ConnectedSocket() client: Socket,
    @MessageBody() data: { instrument: string; timeframe: Timeframe },
  ): void {
    const room = `candles:${data.instrument}:${data.timeframe}`;
    client.join(room);
    this.logger.log(`Client ${client.id} subscribed to ${room}`);
  }

  @SubscribeMessage('subscribeSignals')
  handleSubscribeSignals(@ConnectedSocket() client: Socket): void {
    client.join('signals');
    this.logger.log(`Client ${client.id} subscribed to signals`);
  }

  @SubscribeMessage('subscribeTrades')
  handleSubscribeTrades(
    @ConnectedSocket() client: Socket,
    @MessageBody() data: { accountId: string },
  ): void {
    const room = `trades:${data.accountId}`;
    client.join(room);
    this.logger.log(`Client ${client.id} subscribed to ${room}`);
  }

  @SubscribeMessage('subscribeAlerts')
  handleSubscribeAlerts(@ConnectedSocket() client: Socket): void {
    client.join('alerts');
    this.logger.log(`Client ${client.id} subscribed to alerts`);
  }

  @SubscribeMessage('subscribeKillSwitch')
  handleSubscribeKillSwitch(@ConnectedSocket() client: Socket): void {
    client.join('kill_switch');
    this.logger.log(`Client ${client.id} subscribed to kill_switch`);
  }

  @SubscribeMessage('subscribeAutopilot')
  handleSubscribeAutopilot(
    @ConnectedSocket() client: Socket,
    @MessageBody() data: { userId: string },
  ): void {
    const room = `autopilot:${data.userId}`;
    client.join(room);
    this.logger.log(`Client ${client.id} subscribed to ${room}`);
  }
  @SubscribeMessage('subscribeBacktest')
  handleSubscribeBacktest(@ConnectedSocket() client: Socket): void {
    client.join('backtest');
    this.logger.log(`Client ${client.id} subscribed to backtest`);
  }

  @SubscribeMessage('subscribeReconciliation')
  handleSubscribeReconciliation(@ConnectedSocket() client: Socket): void {
    client.join('reconciliation');
    this.logger.log(`Client ${client.id} subscribed to reconciliation`);
  }

  @SubscribeMessage('subscribeHealth')
  handleSubscribeHealth(@ConnectedSocket() client: Socket): void {
    client.join('health');
    this.logger.log(`Client ${client.id} subscribed to health`);
  }

  // --- Server-push emitters (emit to rooms) ---

  emitCandleUpdate(instrument: string, timeframe: Timeframe, candle: Candle): void {
    const room = `candles:${instrument}:${timeframe}`;
    this.server.to(room).emit('candleUpdate', candle);
  }

  emitSignal(signal: Signal): void {
    this.server.to('signals').emit('signal', signal);
  }

  emitTradeExecution(accountId: string, trade: TradeExecutionResult): void {
    this.server.to(`trades:${accountId}`).emit('tradeExecution', trade);
  }

  emitAlertTriggered(alert: AlertTriggered): void {
    this.server.to('alerts').emit('alertTriggered', alert);
  }

  emitKillSwitchStatus(status: KillSwitchStatus): void {
    this.server.to('kill_switch').emit('killSwitchStatus', status);
  }

  // --- Autopilot emitters (scoped to autopilot:{userId} rooms) ---

  emitAutopilotStateChange(userId: string, payload: AutopilotStateChangePayload): void {
    this.server.to(`autopilot:${userId}`).emit('autopilot:state_change', payload);
  }

  emitMasterAutopilotChange(payload: MasterAutopilotChangePayload): void {
    this.server.emit('autopilot:master_change', payload);
  }

  emitStrategyOverlay(userId: string, payload: StrategyOverlayPayload): void {
    this.server.to(`autopilot:${userId}`).emit('autopilot:strategy_overlay', payload);
  }

  emitTradeEntry(userId: string, payload: TradeEntryPayload): void {
    this.server.to(`autopilot:${userId}`).emit('autopilot:trade_entry', payload);
  }

  emitTradeExit(userId: string, payload: TradeExitPayload): void {
    this.server.to(`autopilot:${userId}`).emit('autopilot:trade_exit', payload);
  }
  // --- Backtest emitters ---

  emitBacktestUpdate(payload: BacktestUpdatePayload): void {
    this.server.to('backtest').emit('backtest:update', payload);
  }

  // --- Account sync emitter ---

  emitAccountSync(payload: { syncedAt: string }): void {
    this.server.emit('account:sync', payload);
  }

  // --- Reconciliation emitters ---

  emitReconciliationDiscrepancy(payload: ReconciliationDiscrepancyPayload): void {
    this.server.to('reconciliation').emit('reconciliation:discrepancy', payload);
  }

  // --- Event sourcing emitters ---

  @SubscribeMessage('subscribeEvents')
  handleSubscribeEvents(@ConnectedSocket() client: Socket): void {
    client.join('events');
    this.logger.log(`Client ${client.id} subscribed to events`);
  }

  emitNewEvent(event: unknown): void {
    this.server.to('events').emit('event:new', event);
  }

  // --- Circuit breaker & health emitters ---

  emitCircuitBreakerStateChange(payload: {
    name: string;
    previousState: string;
    newState: string;
    timestamp: string;
  }): void {
    this.server.to('health').emit('circuitBreaker:stateChange', payload);
  }

  emitConsumerLagAlert(payload: {
    stream: string;
    group: string;
    lag: number;
    threshold: number;
    timestamp: string;
  }): void {
    this.server.to('health').emit('consumerLag:alert', payload);
  }

}
