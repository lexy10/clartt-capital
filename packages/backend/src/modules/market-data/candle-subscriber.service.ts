import {
  Injectable,
  Logger,
  OnModuleInit,
  OnModuleDestroy,
} from '@nestjs/common';
import Redis from 'ioredis';
import { MarketDataService } from './market-data.service';
import { TradingGateway } from '../gateway/trading.gateway';
import { Timeframe } from '../../common/types';

const CANDLE_CHANNEL = 'candles:updates';

/**
 * Higher timeframes to aggregate from 1m candles.
 * Each entry: [timeframe label, bucket size in minutes].
 */
const AGGREGATION_TARGETS: [string, number][] = [
  ['5m', 5],
  ['15m', 15],
  ['30m', 30],
  ['1h', 60],
  ['4h', 240],
  ['1d', 1440],
];

/** In-memory bucket for an in-progress aggregated candle. */
interface CandleBucket {
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  bucketStart: Date; // floored timestamp for this bucket
}

@Injectable()
export class CandleSubscriberService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(CandleSubscriberService.name);
  private subscriber: Redis | null = null;

  /**
   * In-memory aggregation state.
   * Key: `${instrument}:${timeframe}` → current bucket being built.
   */
  private buckets = new Map<string, CandleBucket>();

  constructor(
    private readonly marketDataService: MarketDataService,
    private readonly tradingGateway: TradingGateway,
  ) {}

  async onModuleInit(): Promise<void> {
    const host = process.env.REDIS_HOST || 'redis';
    const port = parseInt(process.env.REDIS_PORT || '6379', 10);

    this.subscriber = new Redis({
      host,
      port,
      maxRetriesPerRequest: null,
      retryStrategy: (times: number) => Math.min(times * 200, 5000),
    });

    this.subscriber.on('error', (err) => {
      this.logger.error(`Redis subscriber error: ${err.message}`);
    });

    this.subscriber.on('message', (channel: string, message: string) => {
      if (channel === CANDLE_CHANNEL) {
        this.handleCandleMessage(message);
      }
    });

    await this.subscriber.subscribe(CANDLE_CHANNEL);
    this.logger.log(`Subscribed to Redis channel: ${CANDLE_CHANNEL}`);
  }

  async onModuleDestroy(): Promise<void> {
    if (this.subscriber) {
      await this.subscriber.unsubscribe(CANDLE_CHANNEL);
      await this.subscriber.quit();
      this.subscriber = null;
      this.logger.log('Redis subscriber connection closed');
    }
  }

  /**
   * Handle an incoming 1m candle from Redis.
   * 1. Persist + emit the 1m candle as-is (incomplete — still building).
   * 2. Aggregate into each higher timeframe bucket and persist + emit those.
   * 3. Mark the previous 1m candle as completed if we've moved to a new minute.
   */
  private prev1mTimestamp = new Map<string, Date>(); // instrument → last 1m bucket start

  private handleCandleMessage(message: string): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(message);
    } catch {
      this.logger.warn(`Malformed JSON on ${CANDLE_CHANNEL}, skipping`);
      return;
    }

    if (!this.isValidCandle(parsed)) {
      this.logger.warn(`Invalid candle payload on ${CANDLE_CHANNEL}, skipping`);
      return;
    }

    const raw = parsed as Record<string, unknown>;
    const instrument = raw.instrument as string;
    const open = raw.open as number;
    const high = raw.high as number;
    const low = raw.low as number;
    const close = raw.close as number;
    const volume = raw.volume as number;
    const completed = raw.completed === true;
    const timestamp = new Date(raw.timestamp as string);
    // Floor to the minute — safety net for any source sending non-zero seconds
    timestamp.setSeconds(0, 0);

    // Mark previous 1m candle as completed if we've moved to a new minute
    const prev1m = this.prev1mTimestamp.get(instrument);
    if (prev1m && prev1m.getTime() !== timestamp.getTime()) {
      this.marketDataService
        .markCompleted(instrument, '1m', prev1m)
        .catch(() => {});
    }
    this.prev1mTimestamp.set(instrument, timestamp);

    // 1. Persist and emit the 1m candle.
    //    If the streamer flagged it as completed (final snapshot on minute rollover),
    //    use force upsert so the corrected OHLCV overwrites even if already marked completed.
    this.persistAndEmit(instrument, '1m', open, high, low, close, volume, timestamp, completed);

    // 2. Aggregate into higher timeframes
    for (const [tf, minutes] of AGGREGATION_TARGETS) {
      this.aggregateCandle(instrument, tf, minutes, open, high, low, close, volume, timestamp);
    }
  }

  /**
   * Aggregate a 1m candle into a higher-timeframe bucket.
   * If the 1m candle belongs to the current bucket, merge it.
   * If it starts a new bucket, mark the old one as completed and start fresh.
   */
  private aggregateCandle(
    instrument: string,
    timeframe: string,
    bucketMinutes: number,
    open: number,
    high: number,
    low: number,
    close: number,
    volume: number,
    timestamp: Date,
  ): void {
    const bucketStart = this.floorToInterval(timestamp, bucketMinutes);
    const key = `${instrument}:${timeframe}`;
    const existing = this.buckets.get(key);

    if (existing && existing.bucketStart.getTime() === bucketStart.getTime()) {
      // Same bucket — merge: keep first open, update high/low/close, sum volume
      existing.high = Math.max(existing.high, high);
      existing.low = Math.min(existing.low, low);
      existing.close = close;
      existing.volume += volume;
    } else {
      // New bucket — mark the old one as completed before starting fresh
      if (existing) {
        this.marketDataService
          .upsertCandle(
            {
              instrument,
              timeframe,
              open: existing.open,
              high: existing.high,
              low: existing.low,
              close: existing.close,
              volume: existing.volume,
              timestamp: existing.bucketStart,
              completed: true,
            },
            true, // force — finalize the completed candle
          )
          .catch((err) => {
            this.logger.error(
              `Failed to finalize ${instrument}:${timeframe}: ${err.message}`,
            );
          });
      }

      this.buckets.set(key, {
        open,
        high,
        low,
        close,
        volume,
        bucketStart,
      });
    }

    // Always emit the current state of the bucket (intermediate update)
    const bucket = this.buckets.get(key)!;
    this.persistAndEmit(
      instrument,
      timeframe,
      bucket.open,
      bucket.high,
      bucket.low,
      bucket.close,
      bucket.volume,
      bucket.bucketStart,
    );
  }

  /**
   * Floor a timestamp to the start of its containing interval.
   * For 1d, floors to midnight UTC.
   */
  private floorToInterval(date: Date, intervalMinutes: number): Date {
    if (intervalMinutes >= 1440) {
      // Daily: floor to midnight UTC
      const d = new Date(date);
      d.setUTCHours(0, 0, 0, 0);
      return d;
    }
    const ms = intervalMinutes * 60 * 1000;
    return new Date(Math.floor(date.getTime() / ms) * ms);
  }

  /** Persist a candle to the DB and emit via WebSocket.
   *  When force=true, overwrites even if the candle is already marked completed. */
  private persistAndEmit(
    instrument: string,
    timeframe: string,
    open: number,
    high: number,
    low: number,
    close: number,
    volume: number,
    timestamp: Date,
    force = false,
  ): void {
    this.marketDataService
      .upsertCandle(
        { instrument, timeframe, open, high, low, close, volume, timestamp, completed: force },
        force,
      )
      .catch((err) => {
        this.logger.error(
          `Failed to upsert candle ${instrument}:${timeframe}: ${err.message}`,
        );
      });

    this.tradingGateway.emitCandleUpdate(instrument, timeframe as Timeframe, {
      instrument,
      timeframe: timeframe as Timeframe,
      open,
      high,
      low,
      close,
      volume,
      timestamp: timestamp.toISOString(),
    });
  }

  private isValidCandle(data: unknown): boolean {
    if (typeof data !== 'object' || data === null) return false;
    const obj = data as Record<string, unknown>;

    if (typeof obj.instrument !== 'string' || obj.instrument.length === 0) return false;
    if (typeof obj.open !== 'number' || isNaN(obj.open)) return false;
    if (typeof obj.high !== 'number' || isNaN(obj.high)) return false;
    if (typeof obj.low !== 'number' || isNaN(obj.low)) return false;
    if (typeof obj.close !== 'number' || isNaN(obj.close)) return false;
    if (typeof obj.volume !== 'number' || isNaN(obj.volume)) return false;
    if (!obj.timestamp) return false;

    const ts = new Date(obj.timestamp as string);
    if (isNaN(ts.getTime())) return false;

    return true;
  }
}
