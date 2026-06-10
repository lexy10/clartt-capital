import { Injectable, Inject, Logger, OnModuleInit, OnModuleDestroy } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import Redis from 'ioredis';
import { REDIS_CLIENT } from '../../common/modules/redis.module';
import {
  TradingGateway,
  TradeEntryPayload,
  TradeExitPayload,
  StrategyOverlayPayload,
} from './trading.gateway';
import { Signal, TradeExecutionResult } from '../../common/types';
import { TradingAccount } from '../trades/entities/trading-account.entity';
import { Trade } from '../trades/entities/trade.entity';
import { InstrumentsService } from '../instruments/instruments.service';

/**
 * Subscribes to Redis streams and pub/sub channels to forward
 * signals and trade execution results to Dashboard clients via WebSocket.
 *
 * Stream keys:
 *   - signals:stream — signals published by Strategy Engine
 *
 * Pub/Sub channels:
 *   - trades:results — trade execution results and autopilot trade events published by Execution Engine
 *   - strategy:overlays — strategy overlay data (entry zones, exit zones, order blocks) published by Strategy Engine
 */

const SIGNALS_STREAM = 'signals:stream';
const SIGNALS_GROUP = 'backend-gateway';
const SIGNALS_CONSUMER = 'gateway-consumer';
const TRADES_CHANNEL = 'trades:results';
const STRATEGY_OVERLAYS_CHANNEL = 'strategy:overlays';

@Injectable()
export class RedisStreamService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(RedisStreamService.name);
  private subscriberClient: Redis | null = null;
  private running = false;
  private pollTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
    private readonly gateway: TradingGateway,
    @InjectRepository(TradingAccount)
    private readonly tradingAccountRepo: Repository<TradingAccount>,
    @InjectRepository(Trade)
    private readonly tradeRepo: Repository<Trade>,
    private readonly instrumentsService: InstrumentsService,
  ) {}

  async onModuleInit(): Promise<void> {
    await this.ensureConsumerGroup();
    this.startSignalStreamPoller();
    this.startTradeResultSubscriber();
    this.logger.log('Redis stream listeners initialized');
  }

  onModuleDestroy(): void {
    this.running = false;
    if (this.pollTimer) {
      clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
    if (this.subscriberClient) {
      this.subscriberClient.disconnect();
      this.subscriberClient = null;
    }
    this.logger.log('Redis stream listeners stopped');
  }

  /**
   * Ensure the consumer group exists for the signals stream.
   * Creates the group from the latest message ID if it doesn't exist.
   */
  private async ensureConsumerGroup(): Promise<void> {
    try {
      await this.redis.xgroup('CREATE', SIGNALS_STREAM, SIGNALS_GROUP, '$', 'MKSTREAM');
      this.logger.log(`Created consumer group "${SIGNALS_GROUP}" on "${SIGNALS_STREAM}"`);
    } catch (err: any) {
      // BUSYGROUP means the group already exists — that's fine
      if (err?.message?.includes('BUSYGROUP')) {
        this.logger.debug(`Consumer group "${SIGNALS_GROUP}" already exists`);
      } else {
        this.logger.error(`Failed to create consumer group: ${err?.message}`);
      }
    }
  }

  /**
   * Poll the signals:stream using XREADGROUP and forward each signal
   * to connected Dashboard clients via the WebSocket gateway.
   */
  private startSignalStreamPoller(): void {
    this.running = true;
    this.pollSignals();
  }

  private async pollSignals(): Promise<void> {
    if (!this.running) return;

    try {
      const results = await this.redis.xreadgroup(
        'GROUP', SIGNALS_GROUP, SIGNALS_CONSUMER,
        'COUNT', '10',
        'BLOCK', '2000',
        'STREAMS', SIGNALS_STREAM, '>',
      ) as [string, [string, string[]][]][] | null;

      if (results) {
        for (const [, messages] of results) {
          for (const [messageId, fields] of messages) {
            this.handleSignalMessage(messageId, fields);
          }
        }
      }
    } catch (err: any) {
      if (this.running) {
        this.logger.error(`Error polling signals stream: ${err?.message}`);
      }
    }

    // Schedule next poll
    if (this.running) {
      this.pollTimer = setTimeout(() => this.pollSignals(), 50);
    }
  }

  private handleSignalMessage(messageId: string, fields: string[]): void {
    try {
      // fields is a flat array: ['data', '{"id":...}']
      const dataIndex = fields.indexOf('data');
      if (dataIndex === -1 || dataIndex + 1 >= fields.length) {
        this.logger.warn(`Signal message ${messageId} missing "data" field`);
        return;
      }

      const signal: Signal = JSON.parse(fields[dataIndex + 1]);
      this.gateway.emitSignal(signal);
      this.logger.debug(`Forwarded signal ${signal.id} to WebSocket clients`);

      // Acknowledge the message
      this.redis.xack(SIGNALS_STREAM, SIGNALS_GROUP, messageId).catch((err) => {
        this.logger.error(`Failed to ACK signal message ${messageId}: ${err?.message}`);
      });
    } catch (err: any) {
      this.logger.error(`Failed to process signal message ${messageId}: ${err?.message}`);
    }
  }

  /**
   * Subscribe to the trades:results and strategy:overlays pub/sub channels to forward
   * trade execution results, autopilot trade events, and strategy overlay data
   * to Dashboard clients via WebSocket.
   */
  private startTradeResultSubscriber(): void {
    // Create a dedicated subscriber client (ioredis requires separate client for pub/sub)
    this.subscriberClient = this.redis.duplicate();

    this.subscriberClient.subscribe(TRADES_CHANNEL, STRATEGY_OVERLAYS_CHANNEL, (err) => {
      if (err) {
        this.logger.error(`Failed to subscribe to pub/sub channels: ${err.message}`);
      } else {
        this.logger.log(`Subscribed to pub/sub channels: trades, overlays`);
      }
    });

    this.subscriberClient.on('message', (channel: string, message: string) => {
      if (channel === TRADES_CHANNEL) {
        this.handleTradeResult(message);
      } else if (channel === STRATEGY_OVERLAYS_CHANNEL) {
        this.handleStrategyOverlay(message);
      }
    });
  }

  private handleTradeResult(message: string): void {
    try {
      const parsed = JSON.parse(message);

      // Check if this is an autopilot trade event (has a `type` field)
      if (parsed.type === 'trade_entry' && parsed.userId) {
        const payload: TradeEntryPayload = parsed;
        this.gateway.emitTradeEntry(parsed.userId, payload);
        // Persist to trades table (fire-and-forget — don't block WebSocket forwarding)
        this.persistTradeEntry(payload).catch((err) => {
          this.logger.error(`Failed to persist trade entry: ${err?.message}`);
        });
        this.logger.debug(`Forwarded autopilot trade_entry for user ${parsed.userId}`);
        return;
      }

      if (parsed.type === 'trade_exit' && parsed.userId) {
        const payload: TradeExitPayload = parsed;
        this.gateway.emitTradeExit(parsed.userId, payload);
        this.persistTradeExit(payload).catch((err) => {
          this.logger.error(`Failed to persist trade exit: ${err?.message}`);
        });
        this.logger.debug(`Forwarded autopilot trade_exit for user ${parsed.userId}`);
        return;
      }

      // Standard trade execution result (no type field) — legacy path
      const trade: TradeExecutionResult = parsed;
      this.gateway.emitTradeExecution(trade.account_id, trade);
      this.logger.debug(`Forwarded trade result ${trade.id} for account ${trade.account_id}`);
    } catch (err: any) {
      this.logger.error(`Failed to process trade result: ${err?.message}`);
    }
  }

  /**
   * Persist a trade_entry event to the `trades` table. Used so trade history
   * exists for analytics, performance, and the dashboard — without it, trades
   * fire but leave no DB trace.
   */
  private async persistTradeEntry(payload: any): Promise<void> {
    const t = payload?.trade;
    if (!t) return;

    // Skip rejected/errored executions — they're noise in trade history
    const status = String(t.status || '').toLowerCase();
    if (status !== 'filled' && status !== 'partial') return;

    // Avoid duplicate insertion if this trade ID already exists
    const existing = await this.tradeRepo.findOne({ where: { id: t.id } });
    if (existing) {
      this.logger.debug(`Trade ${t.id} already persisted, skipping`);
      return;
    }

    const trade = this.tradeRepo.create({
      id: t.id,
      signalId: t.signalId ?? null,
      accountId: payload.accountId ?? t.accountId ?? null,
      brokerOrderId: t.orderId != null ? String(t.orderId) : null,
      direction: t.direction,
      entryPrice: t.entryPrice != null ? String(t.entryPrice) : null,
      fillPrice: t.fillPrice != null ? String(t.fillPrice) : null,
      positionSize: String(t.positionSize ?? 0),
      executionLatencyMs: t.executionLatencyMs != null ? Math.round(t.executionLatencyMs) : null,
      slippage: t.slippage != null ? String(t.slippage) : null,
      spreadAtExecution: t.spreadAtExecution != null ? String(t.spreadAtExecution) : null,
      status: t.status,
      rejectionReason: t.rejectionReason ?? null,
      openedAt: t.executedAt ? new Date(t.executedAt) : new Date(),
    });
    await this.tradeRepo.save(trade);
    this.logger.log(`Persisted trade ${t.id} (order ${t.orderId}) to DB`);
  }

  /**
   * Update an existing trade row with exit data when a trade_exit event fires.
   * Looks up by trade ID first, falling back to broker order ID.
   */
  private async persistTradeExit(payload: any): Promise<void> {
    const t = payload?.trade;
    if (!t) return;

    // Find the open trade — try ID first, then broker order ID
    let existing: Trade | null = null;
    if (t.id) {
      existing = await this.tradeRepo.findOne({ where: { id: t.id } });
    }
    if (!existing && t.orderId) {
      existing = await this.tradeRepo.findOne({ where: { brokerOrderId: String(t.orderId) } });
    }
    if (!existing) {
      this.logger.warn(`Trade exit for unknown trade (id=${t.id}, orderId=${t.orderId}) — skipping`);
      return;
    }

    existing.exitPrice = t.exitPrice != null ? String(t.exitPrice) : existing.exitPrice;
    existing.profitLoss = t.profitLoss != null ? String(t.profitLoss) : existing.profitLoss;
    existing.closedAt = t.executedAt ? new Date(t.executedAt) : new Date();
    if (t.status) existing.status = t.status;
    await this.tradeRepo.save(existing);
    this.logger.log(`Updated trade ${existing.id} with exit data`);
  }

  private async handleStrategyOverlay(message: string): Promise<void> {
    try {
      const parsed = JSON.parse(message);

      // Resolve userId: prefer explicit userId, otherwise look up from accountId
      let userId = parsed.userId;
      if (!userId && parsed.accountId) {
        const account = await this.tradingAccountRepo.findOne({
          where: { id: parsed.accountId },
          select: ['userId'],
        });
        if (account) {
          userId = account.userId;
        } else {
          this.logger.warn(`Strategy overlay: trading account ${parsed.accountId} not found`);
          return;
        }
      }

      if (!userId) {
        this.logger.warn('Strategy overlay message missing both userId and accountId fields');
        return;
      }

      const payload: StrategyOverlayPayload = parsed;
      this.gateway.emitStrategyOverlay(userId, payload);
      this.logger.debug(`Forwarded strategy overlay for user ${userId}`);
    } catch (err: any) {
      this.logger.error(`Failed to process strategy overlay: ${err?.message}`);
    }
  }

}
