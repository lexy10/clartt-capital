import {
  Injectable,
  Inject,
  Logger,
  OnModuleInit,
  OnModuleDestroy,
} from '@nestjs/common';
import Redis from 'ioredis';
import { REDIS_CLIENT } from '../../common/modules/redis.module';
import { StrategiesService } from './strategies.service';
import { TradingGateway, BacktestUpdatePayload } from '../gateway/trading.gateway';

const RESULTS_STREAM = 'backtest:results';
const RESULTS_GROUP = 'backend-backtest';
const RESULTS_CONSUMER = 'backtest-result-consumer';
const STATUS_CHANNEL = 'backtest:status';

@Injectable()
export class BacktestResultConsumer implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(BacktestResultConsumer.name);
  private subscriberClient: Redis | null = null;
  private running = false;
  private pollTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
    private readonly strategiesService: StrategiesService,
    private readonly gateway: TradingGateway,
  ) {}

  async onModuleInit(): Promise<void> {
    await this.cleanupStaleBacktests();
    await this.ensureConsumerGroup();
    this.startResultStreamPoller();
    this.startStatusSubscriber();
    this.logger.log('Backtest result consumer initialized');
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
    this.logger.log('Backtest result consumer stopped');
  }

  /**
   * On startup, mark any stale running/pending backtests as failed.
   * These are leftovers from a previous crash or restart.
   */
  private async cleanupStaleBacktests(): Promise<void> {
    try {
      const count = await this.strategiesService.failStaleBacktests();
      if (count > 0) {
        this.logger.log(`Marked ${count} stale backtest(s) as failed on startup`);
      }
    } catch (err: any) {
      this.logger.error(`Failed to cleanup stale backtests: ${err?.message}`);
    }
  }

  /**
   * Ensure the consumer group exists for the results stream.
   * Creates the group from the latest message ID if it doesn't exist.
   */
  private async ensureConsumerGroup(): Promise<void> {
    try {
      await this.redis.xgroup(
        'CREATE',
        RESULTS_STREAM,
        RESULTS_GROUP,
        '$',
        'MKSTREAM',
      );
      this.logger.log(
        `Created consumer group "${RESULTS_GROUP}" on "${RESULTS_STREAM}"`,
      );
    } catch (err: any) {
      if (err?.message?.includes('BUSYGROUP')) {
        this.logger.debug(`Consumer group "${RESULTS_GROUP}" already exists`);
      } else {
        this.logger.error(`Failed to create consumer group: ${err?.message}`);
      }
    }
  }

  /**
   * Poll the backtest:results stream using XREADGROUP and process each result.
   */
  private startResultStreamPoller(): void {
    this.running = true;
    this.pollResults();
  }

  private async pollResults(): Promise<void> {
    if (!this.running) return;

    try {
      const results = (await this.redis.xreadgroup(
        'GROUP',
        RESULTS_GROUP,
        RESULTS_CONSUMER,
        'COUNT',
        '10',
        'BLOCK',
        '2000',
        'STREAMS',
        RESULTS_STREAM,
        '>',
      )) as [string, [string, string[]][]][] | null;

      if (results) {
        for (const [, messages] of results) {
          for (const [messageId, fields] of messages) {
            await this.handleResultMessage(messageId, fields);
          }
        }
      }
    } catch (err: any) {
      if (this.running) {
        this.logger.error(
          `Error polling results stream: ${err?.message}`,
        );
      }
    }

    // Schedule next poll
    if (this.running) {
      this.pollTimer = setTimeout(() => this.pollResults(), 50);
    }
  }

  private async handleResultMessage(
    messageId: string,
    fields: string[],
  ): Promise<void> {
    try {
      const dataIndex = fields.indexOf('data');
      if (dataIndex === -1 || dataIndex + 1 >= fields.length) {
        this.logger.warn(
          `Result message ${messageId} missing "data" field`,
        );
        await this.ack(messageId);
        return;
      }

      let parsed: any;
      try {
        parsed = JSON.parse(fields[dataIndex + 1]);
      } catch {
        this.logger.error(
          `Invalid JSON in result message ${messageId}`,
        );
        await this.ack(messageId);
        return;
      }

      const resultId = parsed.result_id;
      if (!resultId) {
        this.logger.warn(
          `Result message ${messageId} missing result_id`,
        );
        await this.ack(messageId);
        return;
      }

      if (parsed.status === 'completed' && parsed.stats) {
        await this.strategiesService.updateBacktestResult(resultId, {
          winRate: parsed.stats.win_rate,
          maxDrawdown: parsed.stats.max_drawdown,
          sharpeRatio: parsed.stats.sharpe_ratio,
          profitFactor: parsed.stats.profit_factor,
          expectancy: parsed.stats.expectancy,
          totalTrades: parsed.stats.total_trades,
          winningTrades: parsed.stats.winning_trades,
          losingTrades: parsed.stats.losing_trades,
          grossProfit: parsed.stats.gross_profit,
          grossLoss: parsed.stats.gross_loss,
          netProfit: parsed.stats.net_profit,
          averageRr: parsed.stats.average_rr,
          equityCurve: parsed.equity_curve,
          tradeResults: parsed.trade_results,
        });

        // Persist individual trades to backtest_trades table
        if (Array.isArray(parsed.trade_results) && parsed.trade_results.length > 0) {
          try {
            await this.strategiesService.saveBacktestTrades(resultId, parsed.trade_results);
          } catch (err: any) {
            this.logger.error(
              `Failed to save backtest trades for ${resultId}: ${err?.message}`,
            );
          }
        }

        this.gateway.emitBacktestUpdate({
          result_id: resultId,
          strategy_id: parsed.strategy_id,
          status: 'completed',
          win_rate: parsed.stats.win_rate,
          max_drawdown: parsed.stats.max_drawdown,
          sharpe_ratio: parsed.stats.sharpe_ratio,
          profit_factor: parsed.stats.profit_factor,
          expectancy: parsed.stats.expectancy,
          total_trades: parsed.stats.total_trades,
          winning_trades: parsed.stats.winning_trades,
          losing_trades: parsed.stats.losing_trades,
          gross_profit: parsed.stats.gross_profit,
          gross_loss: parsed.stats.gross_loss,
          net_profit: parsed.stats.net_profit,
          average_rr: parsed.stats.average_rr,
          equity_curve: parsed.equity_curve,
          trade_results: parsed.trade_results,
        });
      } else if (parsed.status === 'failed') {
        await this.strategiesService.updateBacktestStatus(
          resultId,
          'failed',
          parsed.error,
        );
        this.gateway.emitBacktestUpdate({
          result_id: resultId,
          strategy_id: parsed.strategy_id,
          status: 'failed',
          error_message: parsed.error,
        });
      }

      await this.ack(messageId);
    } catch (err: any) {
      this.logger.error(
        `Failed to process result message ${messageId}: ${err?.message}`,
      );
      await this.ack(messageId);
    }
  }

  private async ack(messageId: string): Promise<void> {
    try {
      await this.redis.xack(RESULTS_STREAM, RESULTS_GROUP, messageId);
    } catch (err: any) {
      this.logger.error(
        `Failed to ACK result message ${messageId}: ${err?.message}`,
      );
    }
  }

  /**
   * Subscribe to the backtest:status pub/sub channel for real-time status updates.
   */
  private startStatusSubscriber(): void {
    this.subscriberClient = this.redis.duplicate();

    this.subscriberClient.subscribe(STATUS_CHANNEL, (err) => {
      if (err) {
        this.logger.error(
          `Failed to subscribe to ${STATUS_CHANNEL}: ${err.message}`,
        );
      } else {
        this.logger.log(`Subscribed to pub/sub channel "${STATUS_CHANNEL}"`);
      }
    });

    this.subscriberClient.on('message', (_channel: string, message: string) => {
      this.handleStatusMessage(message);
    });
  }

  private async handleStatusMessage(message: string): Promise<void> {
    try {
      let parsed: any;
      try {
        parsed = JSON.parse(message);
      } catch {
        this.logger.error('Invalid JSON in status pub/sub message');
        return;
      }

      const resultId = parsed.result_id;
      if (!resultId) {
        this.logger.warn('Status message missing result_id');
        return;
      }

      await this.strategiesService.updateBacktestStatus(
        resultId,
        parsed.status,
        parsed.error,
      );
      this.gateway.emitBacktestUpdate({
        result_id: resultId,
        strategy_id: parsed.strategy_id,
        status: parsed.status,
        error_message: parsed.error,
      });
    } catch (err: any) {
      this.logger.error(
        `Failed to process status message: ${err?.message}`,
      );
    }
  }
}
