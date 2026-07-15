import { Injectable, Logger, OnModuleInit, OnModuleDestroy, Inject, HttpException, HttpStatus } from '@nestjs/common';
import { HttpService } from '@nestjs/axios';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { firstValueFrom } from 'rxjs';
import Redis from 'ioredis';
import { Candle } from './entities/candle.entity';
import { InstrumentsService, INSTRUMENT_ACTIVATED_CHANNEL } from '../instruments/instruments.service';
import { MarketDataService } from './market-data.service';
import { CircuitBreaker } from '../../common/circuit-breaker/circuit-breaker';
import { EXECUTION_ENGINE_CIRCUIT_BREAKER } from '../../common/circuit-breaker/circuit-breaker.module';

const BACKFILL_MONTHS = 12;

/** Higher timeframes to aggregate from 1m candles, with bucket size in minutes. */
const AGGREGATION_TARGETS: [string, number][] = [
  ['5m', 5],
  ['15m', 15],
  ['30m', 30],
  ['1h', 60],
  ['4h', 240],
  ['1d', 1440],
];

// Chunk size in days for 1m backfill — must stay within Deriv's 5000-candle
// limit per request.  US30 produces ~840 1m candles/day, so 5 days ≈ 4200
// candles which fits comfortably under the 5000 cap.
const CHUNK_DAYS_1M = 5;

// Health check interval in milliseconds (60 seconds)
const HEALTH_CHECK_INTERVAL_MS = 60_000;

// Micro-backfill interval — patch recent 1m gaps every 60 seconds
const MICRO_BACKFILL_INTERVAL_MS = 60_000;
// How many minutes back to check for gaps in micro-backfill (72 hours)
const MICRO_BACKFILL_LOOKBACK_MINUTES = 4320;

// Periodic full gap-fill interval (30 minutes)
const PERIODIC_GAPFILL_INTERVAL_MS = 30 * 60_000;

@Injectable()
export class BackfillService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(BackfillService.name);
  private healthCheckInterval: NodeJS.Timeout | null = null;
  private microBackfillInterval: NodeJS.Timeout | null = null;
  private periodicGapFillInterval: NodeJS.Timeout | null = null;
  private isRecovering = false;
  private isPeriodicGapFilling = false;
  private activationSub: Redis | null = null;

  constructor(
    private readonly instrumentsService: InstrumentsService,
    private readonly marketDataService: MarketDataService,
    private readonly httpService: HttpService,
    @InjectRepository(Candle)
    private readonly candleRepo: Repository<Candle>,
    @Inject(EXECUTION_ENGINE_CIRCUIT_BREAKER) private readonly circuitBreaker: CircuitBreaker,
  ) {}

  async onModuleInit(): Promise<void> {
    // Delay slightly to let execution engine boot up, then gap-fill + stream
    setTimeout(() => this.autoStartWithGapFill(), 10_000);

    // Start periodic health check after initial startup
    setTimeout(() => this.startHealthCheckLoop(), 30_000);

    // Start periodic micro-backfill to patch recent 1m gaps
    setTimeout(() => this.startMicroBackfillLoop(), 60_000);

    // Start periodic full gap-fill every 30 minutes
    setTimeout(() => this.startPeriodicGapFillLoop(), 5 * 60_000);

    // Listen for instrument activations (published by InstrumentsService) so a
    // newly-activated instrument gets backfilled + streamed immediately rather
    // than waiting for the next periodic gap-fill. A dedicated connection is
    // required — a subscribed client can't issue other commands.
    try {
      this.activationSub = new Redis({
        host: process.env.REDIS_HOST || 'redis',
        port: parseInt(process.env.REDIS_PORT || '6379', 10),
        maxRetriesPerRequest: null,
      });
      this.activationSub.on('error', (err) =>
        this.logger.warn(`Activation subscriber error: ${err.message}`),
      );
      this.activationSub.on('message', (_channel: string, symbol: string) => {
        this.logger.log(`Instrument ${symbol} activated — backfilling`);
        this.triggerBackfill().catch((err) =>
          this.logger.error(`Backfill on activation failed: ${err.message}`),
        );
      });
      await this.activationSub.subscribe(INSTRUMENT_ACTIVATED_CHANNEL);
    } catch (err) {
      this.logger.error(`Failed to subscribe to instrument activations: ${(err as Error).message}`);
    }
  }

  onModuleDestroy(): void {
    if (this.activationSub) {
      this.activationSub.disconnect();
      this.activationSub = null;
    }
    if (this.healthCheckInterval) {
      clearInterval(this.healthCheckInterval);
      this.healthCheckInterval = null;
    }
    if (this.microBackfillInterval) {
      clearInterval(this.microBackfillInterval);
      this.microBackfillInterval = null;
    }
    if (this.periodicGapFillInterval) {
      clearInterval(this.periodicGapFillInterval);
      this.periodicGapFillInterval = null;
    }
  }

  /**
   * Start periodic health check loop that monitors stream status
   * and restarts streaming if it goes down.
   */
  private startHealthCheckLoop(): void {
    this.logger.log('Starting stream health check loop (60s interval)');
    this.healthCheckInterval = setInterval(
      () => this.checkAndRecoverStream(),
      HEALTH_CHECK_INTERVAL_MS,
    );
  }

  /**
   * Start periodic micro-backfill loop that patches recent 1m gaps
   * by fetching the last few minutes from Deriv every 60s.
   */
  private startMicroBackfillLoop(): void {
    this.logger.log('Starting micro-backfill loop (60s interval)');
    this.microBackfillInterval = setInterval(
      () => this.microBackfillRecent(),
      MICRO_BACKFILL_INTERVAL_MS,
    );
  }

  /**
   * Fetch the last MICRO_BACKFILL_LOOKBACK_MINUTES of 1m candles from Deriv
   * for each active instrument and upsert any missing ones.
   */
  private async microBackfillRecent(): Promise<void> {
    const engineBaseUrl =
      process.env.EXECUTION_ENGINE_URL || 'http://execution-engine:8002';

    let instruments;
    try {
      instruments = await this.instrumentsService.findAllActive();
    } catch {
      return;
    }

    const derivInstruments = instruments.filter((i) => i.derivSymbol);
    if (derivInstruments.length === 0) return;

    const now = new Date();
    const lookbackStart = new Date(now.getTime() - MICRO_BACKFILL_LOOKBACK_MINUTES * 60_000);

    for (const inst of derivInstruments) {
      try {
        const response = await this.circuitBreaker.execute(
          () => firstValueFrom(
            this.httpService.post(
              `${engineBaseUrl}/api/candles/historical`,
              {
                broker_symbol: inst.derivSymbol,
                timeframe: '1m',
                start_date: lookbackStart.toISOString(),
                end_date: now.toISOString(),
              },
              { timeout: 30_000 },
            ),
          ),
          () => {
            throw new HttpException('Execution Engine is unavailable', HttpStatus.SERVICE_UNAVAILABLE);
          },
        );

        const candles = response.data;
        if (!Array.isArray(candles) || candles.length === 0) continue;

        let filled = 0;
        for (const c of candles) {
          // Floor timestamp to the minute — Deriv sometimes returns
          // non-zero seconds (e.g. :38) which creates duplicate candles
          const raw = new Date(c.timestamp);
          const ts = new Date(raw);
          ts.setSeconds(0, 0);

          await this.marketDataService.upsertCandle(
            {
              instrument: inst.symbol,
              timeframe: '1m',
              open: c.open,
              high: c.high,
              low: c.low,
              close: c.close,
              volume: c.volume || 0,
              timestamp: ts,
              completed: true,
            },
            true,
          );
          filled++;
        }

        if (filled > 0) {
          this.logger.log(
            `Micro-backfill: patched ${filled} 1m candle(s) for ${inst.symbol}`,
          );
        }

        // Always reaggregate higher TFs for the lookback window so they stay current
        await this.reaggregateHigherTimeframes(inst.symbol, lookbackStart, now);
      } catch (err) {
        this.logger.debug(
          `Micro-backfill failed for ${inst.symbol}: ${err.message}`,
        );
      }
    }
  }

  /**
   * Start periodic full gap-fill loop that runs gapFillInstrument
   * for all active instruments every 30 minutes.
   */
  private startPeriodicGapFillLoop(): void {
    this.logger.log('Starting periodic gap-fill loop (30m interval)');
    this.periodicGapFillInterval = setInterval(
      () => this.periodicGapFill(),
      PERIODIC_GAPFILL_INTERVAL_MS,
    );
  }

  /**
   * Run full gap-fill + higher TF reaggregation for all active instruments.
   * Catches up any 1m gaps and rebuilds higher TFs from 1m data.
   */
  private async periodicGapFill(): Promise<void> {
    if (this.isPeriodicGapFilling) {
      this.logger.debug('Periodic gap-fill already running, skipping');
      return;
    }
    this.isPeriodicGapFilling = true;

    try {
      const instruments = await this.instrumentsService.findAllActive();
      const derivInstruments = instruments.filter((i) => i.derivSymbol);
      if (derivInstruments.length === 0) return;

      const engineBaseUrl =
        process.env.EXECUTION_ENGINE_URL || 'http://execution-engine:8002';

      this.logger.log(
        `Periodic gap-fill starting for ${derivInstruments.length} instrument(s)`,
      );

      for (const inst of derivInstruments) {
        await this.gapFillInstrument(inst.symbol, inst.derivSymbol!, engineBaseUrl, inst.type);
        await this.refillFlatCandles(inst.symbol, inst.derivSymbol!, engineBaseUrl);
      }

      await this.recoverIncompleteCandles();

      this.logger.log('Periodic gap-fill complete');
    } catch (err) {
      this.logger.error(`Periodic gap-fill failed: ${err.message}`);
    } finally {
      this.isPeriodicGapFilling = false;
    }
  }

  /**
   * Check stream status and restart if not active.
   */
  private async checkAndRecoverStream(): Promise<void> {
    if (this.isRecovering) {
      this.logger.debug('Recovery already in progress, skipping health check');
      return;
    }

    const engineBaseUrl =
      process.env.EXECUTION_ENGINE_URL || 'http://execution-engine:8002';

    try {
      const response = await this.circuitBreaker.execute(
        () => firstValueFrom(
          this.httpService.get(`${engineBaseUrl}/api/candles/stream/status`, {
            timeout: 5_000,
          }),
        ),
        () => {
          throw new HttpException('Execution Engine is unavailable', HttpStatus.SERVICE_UNAVAILABLE);
        },
      );

      const status = response.data;
      if (status.active && status.subscription_count > 0) {
        this.logger.debug(
          `Stream healthy: ${status.subscription_count} subscriptions active`,
        );
        return;
      }

      // Stream is not active — trigger recovery
      this.logger.warn(
        `Stream unhealthy (active=${status.active}, subs=${status.subscription_count}), triggering recovery`,
      );
      await this.recoverStream();
    } catch (err) {
      // Execution engine might be down or restarting
      this.logger.warn(`Stream health check failed: ${err.message}`);
      // Wait a bit and try recovery
      setTimeout(() => this.recoverStream(), 5_000);
    }
  }

  /**
   * Recover streaming by restarting subscriptions for all instruments.
   */
  private async recoverStream(): Promise<void> {
    if (this.isRecovering) return;
    this.isRecovering = true;

    try {
      this.logger.log('Recovering candle stream...');

      const instruments = await this.instrumentsService.findAllActive();
      const derivInstruments = instruments.filter((i) => i.derivSymbol);

      if (derivInstruments.length === 0) {
        this.logger.warn('No instruments with derivSymbol found, cannot recover stream');
        return;
      }

      const engineBaseUrl =
        process.env.EXECUTION_ENGINE_URL || 'http://execution-engine:8002';

      const symbolMap: Record<string, string> = {};
      const dataSymbols: string[] = [];
      for (const inst of derivInstruments) {
        symbolMap[inst.derivSymbol!] = inst.symbol;
        dataSymbols.push(inst.derivSymbol!);
      }

      await this.circuitBreaker.execute(
        () => firstValueFrom(
          this.httpService.post(
            `${engineBaseUrl}/api/candles/stream/start`,
            { symbols: dataSymbols, symbol_map: symbolMap },
            { timeout: 10_000 },
          ),
        ),
        () => {
          throw new HttpException('Execution Engine is unavailable', HttpStatus.SERVICE_UNAVAILABLE);
        },
      );

      this.logger.log(
        `Stream recovered: ${dataSymbols.length} instruments (${dataSymbols.join(', ')})`,
      );
    } catch (err) {
      this.logger.error(`Stream recovery failed: ${err.message}`);
    } finally {
      this.isRecovering = false;
    }
  }

  /**
   * On startup: for every active instrument with a derivSymbol,
   * detect candle gaps, backfill 1m from Deriv, re-aggregate higher TFs,
   * then start streaming.
   */
  private async autoStartWithGapFill(): Promise<void> {
    try {
      const instruments = await this.instrumentsService.findAllActive();
      const derivInstruments = instruments.filter((i) => i.derivSymbol);

      if (derivInstruments.length === 0) {
        this.logger.log('No instruments with derivSymbol found, skipping auto-start');
        return;
      }

      const engineBaseUrl =
        process.env.EXECUTION_ENGINE_URL || 'http://execution-engine:8002';

      // symbolMap: deriv_symbol -> instrument_symbol (for Redis candle publishing)
      const symbolMap: Record<string, string> = {};
      const dataSymbols: string[] = [];
      for (const inst of derivInstruments) {
        symbolMap[inst.derivSymbol!] = inst.symbol;
        dataSymbols.push(inst.derivSymbol!);
      }

      // Gap-fill 1m candles + higher timeframes for all instruments in parallel
      await Promise.all(
        derivInstruments.map((inst) =>
          this.gapFillInstrument(
            inst.symbol,
            inst.derivSymbol!,
            engineBaseUrl,
            inst.type,
          ),
        ),
      );

      // Recover any incomplete higher-TF candles left from a previous session.
      // These are candles where the bucket period has passed but they were never
      // finalized (e.g. backend went down mid-hour). Re-aggregate them from 1m data.
      await this.recoverIncompleteCandles();

      // Start streaming using Deriv symbols
      await this.circuitBreaker.execute(
        () => firstValueFrom(
          this.httpService.post(
            `${engineBaseUrl}/api/candles/stream/start`,
            { symbols: dataSymbols, symbol_map: symbolMap },
            { timeout: 10_000 },
          ),
        ),
        () => {
          throw new HttpException('Execution Engine is unavailable', HttpStatus.SERVICE_UNAVAILABLE);
        },
      );
      this.logger.log(
        `Auto-started candle stream for ${dataSymbols.length} instruments: ${dataSymbols.join(', ')}`,
      );
    } catch (err) {
      this.logger.error(`Auto-start with gap-fill failed: ${err.message}`);
    }
  }

  /**
   * Detect gaps in candle data — checks both historical coverage (12 months back)
   * and recent freshness. Backfills any missing ranges.
   */
  private async gapFillInstrument(
    instrumentSymbol: string,
    derivSymbol: string,
    engineBaseUrl: string,
    instrumentType: string = 'index',
  ): Promise<void> {
    try {
      const stats = await this.candleRepo
        .createQueryBuilder('c')
        .select('MIN(c.timestamp)', 'minTs')
        .addSelect('MAX(c.timestamp)', 'maxTs')
        .where('c.instrument = :instrument', { instrument: instrumentSymbol })
        .andWhere('c.timeframe = :timeframe', { timeframe: '1m' })
        .getRawOne();

      const now = new Date();
      const targetHistoricalStart = new Date(now);
      targetHistoricalStart.setMonth(targetHistoricalStart.getMonth() - BACKFILL_MONTHS);

      // Case 1: No data at all — full backfill
      if (!stats?.minTs || !stats?.maxTs) {
        this.logger.log(
          `No 1m data for ${instrumentSymbol}, full backfill from ${targetHistoricalStart.toISOString().slice(0, 10)}`,
        );
        await this.backfill1mChunked(
          instrumentSymbol,
          derivSymbol,
          engineBaseUrl,
          targetHistoricalStart,
          now,
        );
        await this.reaggregateHigherTimeframes(instrumentSymbol, targetHistoricalStart, now);
        // Higher TFs already reaggregated, no need to check gaps
        return;
      }

      const earliestData = new Date(stats.minTs);
      const latestData = new Date(stats.maxTs);

      // Case 2: Check if historical data goes back far enough (within 1 day tolerance)
      const historicalGapMs = earliestData.getTime() - targetHistoricalStart.getTime();
      const historicalGapDays = Math.floor(historicalGapMs / (24 * 60 * 60 * 1000));

      if (historicalGapDays > 1) {
        this.logger.log(
          `Historical gap for ${instrumentSymbol}: earliest data is ${earliestData.toISOString().slice(0, 10)}, ` +
          `need data from ${targetHistoricalStart.toISOString().slice(0, 10)} (${historicalGapDays} days missing)`,
        );
        await this.backfill1mChunked(
          instrumentSymbol,
          derivSymbol,
          engineBaseUrl,
          targetHistoricalStart,
          earliestData,
        );
        await this.reaggregateHigherTimeframes(instrumentSymbol, targetHistoricalStart, earliestData);
      }

      // Case 3: Check if recent data is fresh (within 2 minutes)
      const recentGapMs = now.getTime() - latestData.getTime();
      const recentGapMinutes = Math.floor(recentGapMs / 60_000);

      if (recentGapMinutes < 2) {
        this.logger.debug(`${instrumentSymbol} 1m data is current, no recent gap-fill needed`);
        // Check for internal gaps even if edges are fine
        await this.detectInternalGaps(instrumentSymbol, derivSymbol, engineBaseUrl, instrumentType);
        // Still check higher timeframes even if 1m is current
        await this.detectAndFillHigherTimeframeGaps(instrumentSymbol);
        return;
      }

      this.logger.log(
        `Recent gap for ${instrumentSymbol}: ${recentGapMinutes} minutes (${latestData.toISOString()} → now)`,
      );
      await this.backfill1mChunked(
        instrumentSymbol,
        derivSymbol,
        engineBaseUrl,
        latestData,
        now,
      );
      await this.reaggregateHigherTimeframes(instrumentSymbol, latestData, now);

      // Detect and fill internal (middle) gaps in 1m data
      await this.detectInternalGaps(instrumentSymbol, derivSymbol, engineBaseUrl, instrumentType);

      // After 1m gap-fill is complete, check and fill higher timeframe gaps
      await this.detectAndFillHigherTimeframeGaps(instrumentSymbol);
    } catch (err) {
      this.logger.error(
        `Gap-fill failed for ${instrumentSymbol}: ${err.message}`,
      );
    }
  }

  /**
   * Detect and fill internal (middle) gaps in 1m candle data.
   * Uses SQL LAG() window function to find consecutive candles with timestamps
   * further apart than expected. Different thresholds for 24/7 synthetic markets
   * vs session-based real markets (which have weekends/closures).
   */
  private async detectInternalGaps(
    instrumentSymbol: string,
    derivSymbol: string,
    engineBaseUrl: string,
    instrumentType: string = 'index',
  ): Promise<void> {
    // Synthetic indices trade 24/7 — any gap > 2 minutes means missing candles.
    // (1m candles are 1 min apart, so 2+ min gap = at least 1 missing candle.)
    // Real markets (index, commodity) have overnight closures and weekends,
    // so use a 3-day threshold to avoid backfilling normal closed periods.
    const gapThresholdMinutes = instrumentType === 'synthetic' ? 2 : 4320;

    this.logger.debug(
      `Checking internal 1m gaps for ${instrumentSymbol} (threshold: ${gapThresholdMinutes}min)`,
    );

    try {
      // Use LAG() to find consecutive 1m candles with gaps exceeding the threshold
      const gaps: { gap_start: Date; gap_end: Date; gap_minutes: number }[] =
        await this.candleRepo.query(
          `
          WITH ordered AS (
            SELECT timestamp,
                   LAG(timestamp) OVER (ORDER BY timestamp) AS prev_ts
            FROM candles
            WHERE instrument = $1
              AND timeframe = '1m'
          )
          SELECT prev_ts AS gap_start,
                 timestamp AS gap_end,
                 EXTRACT(EPOCH FROM (timestamp - prev_ts)) / 60 AS gap_minutes
          FROM ordered
          WHERE prev_ts IS NOT NULL
            AND EXTRACT(EPOCH FROM (timestamp - prev_ts)) / 60 > $2
          ORDER BY prev_ts
          `,
          [instrumentSymbol, gapThresholdMinutes],
        );

      if (gaps.length === 0) {
        this.logger.debug(`No internal 1m gaps found for ${instrumentSymbol}`);
        return;
      }

      this.logger.log(
        `Found ${gaps.length} internal 1m gap(s) for ${instrumentSymbol}`,
      );

      let totalFilled = 0;
      for (const gap of gaps) {
        const gapStart = new Date(gap.gap_start);
        const gapEnd = new Date(gap.gap_end);
        const gapMin = Math.round(Number(gap.gap_minutes));

        this.logger.log(
          `Internal gap: ${instrumentSymbol} ${gapStart.toISOString()} → ${gapEnd.toISOString()} (${gapMin} min)`,
        );

        await this.backfill1mChunked(
          instrumentSymbol,
          derivSymbol,
          engineBaseUrl,
          gapStart,
          gapEnd,
        );
        await this.reaggregateHigherTimeframes(instrumentSymbol, gapStart, gapEnd);
        totalFilled++;
      }

      if (totalFilled > 0) {
        this.logger.log(
          `Filled ${totalFilled} internal gap(s) for ${instrumentSymbol}`,
        );
      }
    } catch (err) {
      this.logger.error(
        `Internal gap detection failed for ${instrumentSymbol}: ${err.message}`,
      );
    }
  }

  /**
   * Detect and refill flat candles — candles where open == high == low == close.
   * These are likely bad data from the broker (e.g. stale ticks during low
   * liquidity) and should be replaced with real data from Deriv.
   * Looks back 12 hours to keep the scan bounded.
   */
  private async refillFlatCandles(
    instrumentSymbol: string,
    derivSymbol: string,
    engineBaseUrl: string,
  ): Promise<void> {
    const lookbackMs = 12 * 60 * 60 * 1000; // 12 hours
    const now = new Date();
    const since = new Date(now.getTime() - lookbackMs);

    try {
      const flatCandles: Candle[] = await this.candleRepo
        .createQueryBuilder('c')
        .where('c.instrument = :instrument', { instrument: instrumentSymbol })
        .andWhere('c.timeframe = :timeframe', { timeframe: '1m' })
        .andWhere('c.timestamp >= :since', { since })
        .andWhere('c."open" = c.high')
        .andWhere('c.high = c.low')
        .andWhere('c.low = c.close')
        .orderBy('c.timestamp', 'ASC')
        .getMany();

      if (flatCandles.length === 0) {
        this.logger.debug(`No flat 1m candles found for ${instrumentSymbol} in last 12h`);
        return;
      }

      this.logger.log(
        `Found ${flatCandles.length} flat 1m candle(s) for ${instrumentSymbol}, refilling from Deriv`,
      );

      // Group flat candles into contiguous ranges to minimize API calls
      const ranges: { start: Date; end: Date }[] = [];
      let rangeStart = flatCandles[0].timestamp;
      let rangeEnd = flatCandles[0].timestamp;

      for (let i = 1; i < flatCandles.length; i++) {
        const gap = flatCandles[i].timestamp.getTime() - rangeEnd.getTime();
        // If within 5 minutes, extend the current range
        if (gap <= 5 * 60_000) {
          rangeEnd = flatCandles[i].timestamp;
        } else {
          ranges.push({ start: rangeStart, end: rangeEnd });
          rangeStart = flatCandles[i].timestamp;
          rangeEnd = flatCandles[i].timestamp;
        }
      }
      ranges.push({ start: rangeStart, end: rangeEnd });

      let totalRefilled = 0;
      for (const range of ranges) {
        // Pad the range by 1 minute on each side to ensure we get the edge candles
        const fetchStart = new Date(range.start.getTime() - 60_000);
        const fetchEnd = new Date(range.end.getTime() + 60_000);

        try {
          const response = await this.circuitBreaker.execute(
            () => firstValueFrom(
              this.httpService.post(
                `${engineBaseUrl}/api/candles/historical`,
                {
                  broker_symbol: derivSymbol,
                  timeframe: '1m',
                  start_date: fetchStart.toISOString(),
                  end_date: fetchEnd.toISOString(),
                },
                { timeout: 30_000 },
              ),
            ),
            () => {
              throw new HttpException('Execution Engine is unavailable', HttpStatus.SERVICE_UNAVAILABLE);
            },
          );

          const candles = response.data;
          if (!Array.isArray(candles) || candles.length === 0) continue;

          for (const c of candles) {
            const ts = new Date(c.timestamp);
            ts.setSeconds(0, 0);

            // Only overwrite if the fetched candle is NOT flat itself
            if (c.open === c.high && c.high === c.low && c.low === c.close) continue;

            await this.marketDataService.upsertCandle(
              {
                instrument: instrumentSymbol,
                timeframe: '1m',
                open: c.open,
                high: c.high,
                low: c.low,
                close: c.close,
                volume: c.volume || 0,
                timestamp: ts,
                completed: true,
              },
              true, // force overwrite
            );
            totalRefilled++;
          }

          // Reaggregate higher TFs for this range
          await this.reaggregateHigherTimeframes(instrumentSymbol, range.start, range.end);
        } catch (err) {
          this.logger.error(
            `Flat candle refill failed for ${instrumentSymbol} range ${range.start.toISOString()} → ${range.end.toISOString()}: ${err.message}`,
          );
        }
      }

      if (totalRefilled > 0) {
        this.logger.log(
          `Refilled ${totalRefilled} flat candle(s) for ${instrumentSymbol}`,
        );
      }
    } catch (err) {
      this.logger.error(
        `Flat candle detection failed for ${instrumentSymbol}: ${err.message}`,
      );
    }
  }

  /**
   * Detect and fill gaps in higher timeframes by reaggregating from 1m data.
   * Compares the date range of 1m candles vs each higher timeframe.
   */
  private async detectAndFillHigherTimeframeGaps(
    instrumentSymbol: string,
  ): Promise<void> {
    try {
      // Get the date range of 1m candles
      const oneMinStats = await this.candleRepo
        .createQueryBuilder('c')
        .select('MIN(c.timestamp)', 'minTs')
        .addSelect('MAX(c.timestamp)', 'maxTs')
        .addSelect('COUNT(*)', 'count')
        .where('c.instrument = :instrument', { instrument: instrumentSymbol })
        .andWhere('c.timeframe = :timeframe', { timeframe: '1m' })
        .getRawOne();

      if (!oneMinStats?.minTs || !oneMinStats?.maxTs) {
        this.logger.debug(`No 1m data for ${instrumentSymbol}, skipping higher TF gap check`);
        return;
      }

      const oneMinStart = new Date(oneMinStats.minTs);
      const oneMinEnd = new Date(oneMinStats.maxTs);

      // Check each higher timeframe for gaps
      for (const [tf, minutes] of AGGREGATION_TARGETS) {
        const tfStats = await this.candleRepo
          .createQueryBuilder('c')
          .select('MIN(c.timestamp)', 'minTs')
          .addSelect('MAX(c.timestamp)', 'maxTs')
          .addSelect('COUNT(*)', 'count')
          .where('c.instrument = :instrument', { instrument: instrumentSymbol })
          .andWhere('c.timeframe = :timeframe', { timeframe: tf })
          .getRawOne();

        const tfCount = parseInt(tfStats?.count || '0', 10);

        // Case 1: No data for this timeframe at all
        if (!tfStats?.minTs || !tfStats?.maxTs || tfCount === 0) {
          this.logger.log(
            `No ${tf} data for ${instrumentSymbol}, reaggregating from 1m`,
          );
          await this.reaggregateHigherTimeframes(instrumentSymbol, oneMinStart, oneMinEnd);
          // After reaggregating all TFs, no need to check remaining ones
          return;
        }

        const tfStart = new Date(tfStats.minTs);
        const tfEnd = new Date(tfStats.maxTs);

        // Case 2: Higher TF doesn't cover the full 1m range (historical gap)
        const historicalGapMs = tfStart.getTime() - oneMinStart.getTime();
        const historicalGapMinutes = Math.floor(historicalGapMs / 60_000);

        if (historicalGapMinutes > minutes) {
          this.logger.log(
            `Historical gap in ${tf} for ${instrumentSymbol}: ${tf} starts at ${tfStart.toISOString().slice(0, 10)}, ` +
            `but 1m starts at ${oneMinStart.toISOString().slice(0, 10)}`,
          );
          await this.reaggregateSingleTimeframe(instrumentSymbol, tf, minutes, oneMinStart, tfStart);
        }

        // Case 3: Higher TF doesn't cover recent 1m data (recent gap)
        const recentGapMs = oneMinEnd.getTime() - tfEnd.getTime();
        const recentGapMinutes = Math.floor(recentGapMs / 60_000);

        if (recentGapMinutes > minutes) {
          this.logger.log(
            `Recent gap in ${tf} for ${instrumentSymbol}: ${tf} ends at ${tfEnd.toISOString().slice(0, 16)}, ` +
            `but 1m ends at ${oneMinEnd.toISOString().slice(0, 16)} (${recentGapMinutes} min gap)`,
          );
          await this.reaggregateSingleTimeframe(instrumentSymbol, tf, minutes, tfEnd, oneMinEnd);
        }

        // Case 4: Check if count is reasonable (detect sparse data)
        const expectedCandles = Math.floor(
          (oneMinEnd.getTime() - oneMinStart.getTime()) / (minutes * 60 * 1000),
        );
        const coverageRatio = tfCount / expectedCandles;

        if (coverageRatio < 0.5 && expectedCandles > 10) {
          this.logger.warn(
            `Sparse ${tf} data for ${instrumentSymbol}: ${tfCount} candles vs ~${expectedCandles} expected (${(coverageRatio * 100).toFixed(1)}% coverage), reaggregating`,
          );
          await this.reaggregateSingleTimeframe(instrumentSymbol, tf, minutes, oneMinStart, oneMinEnd);
        }
      }
    } catch (err) {
      this.logger.error(
        `Higher TF gap detection failed for ${instrumentSymbol}: ${err.message}`,
      );
    }
  }

  /**
   * Reaggregate a single higher timeframe from 1m candles for a specific date range.
   */
  private async reaggregateSingleTimeframe(
    instrumentSymbol: string,
    timeframe: string,
    minutes: number,
    startDate: Date,
    endDate: Date,
  ): Promise<void> {
    const rangeCandles = await this.candleRepo
      .createQueryBuilder('c')
      .where('c.instrument = :instrument', { instrument: instrumentSymbol })
      .andWhere('c.timeframe = :timeframe', { timeframe: '1m' })
      .andWhere('c.timestamp >= :start', { start: startDate })
      .andWhere('c.timestamp <= :end', { end: endDate })
      .orderBy('c.timestamp', 'ASC')
      .getMany();

    if (rangeCandles.length === 0) {
      this.logger.debug(`No 1m candles in range for ${instrumentSymbol}:${timeframe}`);
      return;
    }

    const buckets = new Map<
      number,
      { open: number; high: number; low: number; close: number; volume: number; bucketStart: Date }
    >();

    for (const candle of rangeCandles) {
      const bucketStart = this.floorToInterval(candle.timestamp, minutes);
      const key = bucketStart.getTime();
      const existing = buckets.get(key);

      if (existing) {
        existing.high = Math.max(existing.high, candle.high);
        existing.low = Math.min(existing.low, candle.low);
        existing.close = candle.close;
        existing.volume += candle.volume;
      } else {
        buckets.set(key, {
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
          volume: candle.volume,
          bucketStart,
        });
      }
    }

    let count = 0;
    const BATCH_SIZE = 200;
    const entries = Array.from(buckets.values());

    for (let i = 0; i < entries.length; i += BATCH_SIZE) {
      const batch = entries.slice(i, i + BATCH_SIZE);
      await Promise.all(
        batch.map((b) =>
          this.marketDataService.upsertCandle({
            instrument: instrumentSymbol,
            timeframe,
            open: b.open,
            high: b.high,
            low: b.low,
            close: b.close,
            volume: b.volume,
            timestamp: b.bucketStart,
            completed: true,
          }, true),
        ),
      );
      count += batch.length;
    }

    if (count > 0) {
      this.logger.log(
        `Filled ${count} ${timeframe} candles for ${instrumentSymbol} [${startDate.toISOString().slice(0, 10)} → ${endDate.toISOString().slice(0, 10)}]`,
      );
    }
  }

  /**
   * Recover incomplete candles from a previous session.
   * Finds all non-1m candles where completed=false and the bucket period has
   * already passed, then re-aggregates them from 1m data and marks them completed.
   */
  private async recoverIncompleteCandles(): Promise<void> {
    try {
      const now = new Date();
      // Find incomplete higher-TF candles whose bucket period has ended
      const incomplete: { instrument: string; timeframe: string; timestamp: Date }[] =
        await this.candleRepo.query(
          `SELECT DISTINCT instrument, timeframe, timestamp
           FROM candles
           WHERE completed = false
             AND timeframe != '1m'
             AND (
               CASE timeframe
                 WHEN '5m'  THEN timestamp + interval '5 minutes'
                 WHEN '15m' THEN timestamp + interval '15 minutes'
                 WHEN '30m' THEN timestamp + interval '30 minutes'
                 WHEN '1h'  THEN timestamp + interval '1 hour'
                 WHEN '4h'  THEN timestamp + interval '4 hours'
                 WHEN '1d'  THEN timestamp + interval '1 day'
                 ELSE timestamp + interval '1 day'
               END
             ) < $1
           ORDER BY instrument, timeframe, timestamp`,
          [now],
        );

      if (incomplete.length === 0) {
        this.logger.debug('No incomplete candles to recover');
        return;
      }

      this.logger.log(`Recovering ${incomplete.length} incomplete candle(s)`);

      const tfMinutes: Record<string, number> = {
        '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240, '1d': 1440,
      };

      for (const row of incomplete) {
        const minutes = tfMinutes[row.timeframe];
        if (!minutes) continue;

        const bucketStart = new Date(row.timestamp);
        const bucketEnd = new Date(bucketStart.getTime() + minutes * 60 * 1000);

        await this.reaggregateSingleTimeframe(
          row.instrument,
          row.timeframe,
          minutes,
          bucketStart,
          bucketEnd,
        );
      }

      this.logger.log(`Recovered ${incomplete.length} incomplete candle(s)`);
    } catch (err) {
      this.logger.error(`Incomplete candle recovery failed: ${err.message}`);
    }
  }

  /**
   * Manually trigger a full backfill for all active instruments with derivSymbol.
   * Skips instruments that already have fresh data (unless force=true).
   */
  async triggerBackfill(force = false): Promise<void> {
    this.logger.log(`Starting manual backfill (force=${force})`);

    const instruments = await this.instrumentsService.findAllActive();
    const derivInstruments = instruments.filter((i) => i.derivSymbol);

    if (derivInstruments.length === 0) {
      this.logger.warn('No instruments with derivSymbol found, skipping backfill');
      return;
    }

    const engineBaseUrl =
      process.env.EXECUTION_ENGINE_URL || 'http://execution-engine:8002';
    const endDate = new Date();
    const startDate = new Date();
    startDate.setMonth(startDate.getMonth() - BACKFILL_MONTHS);

    await Promise.all(
      derivInstruments.map(async (inst) => {
        if (!force) {
          const isFresh = await this.hasFreshData(inst.symbol, '1m');
          if (isFresh) {
            this.logger.debug(`Skipping ${inst.symbol}:1m — fresh data exists`);
            return;
          }
        }

        await this.backfill1mChunked(
          inst.symbol,
          inst.derivSymbol!,
          engineBaseUrl,
          startDate,
          endDate,
        );

        await this.reaggregateHigherTimeframes(inst.symbol, startDate, endDate);
      }),
    );

    this.logger.log('Backfill complete for all instruments');

    // Start streaming after backfill
    const symbolMap: Record<string, string> = {};
    const dataSymbols: string[] = [];
    for (const inst of derivInstruments) {
      symbolMap[inst.derivSymbol!] = inst.symbol;
      dataSymbols.push(inst.derivSymbol!);
    }

    if (dataSymbols.length > 0) {
      try {
        await this.circuitBreaker.execute(
          () => firstValueFrom(
            this.httpService.post(
              `${engineBaseUrl}/api/candles/stream/start`,
              { symbols: dataSymbols, symbol_map: symbolMap },
              { timeout: 10_000 },
            ),
          ),
          () => {
            throw new HttpException('Execution Engine is unavailable', HttpStatus.SERVICE_UNAVAILABLE);
          },
        );
        this.logger.log(
          `Started candle stream for ${dataSymbols.join(', ')}`,
        );
      } catch (err) {
        this.logger.error(`Failed to start candle stream: ${err.message}`);
      }
    }
  }

  async stopStream(): Promise<void> {
    const engineBaseUrl =
      process.env.EXECUTION_ENGINE_URL || 'http://execution-engine:8002';
    try {
      await this.circuitBreaker.execute(
        () => firstValueFrom(
          this.httpService.post(
            `${engineBaseUrl}/api/candles/stream/stop`,
            {},
            { timeout: 10_000 },
          ),
        ),
        () => {
          throw new HttpException('Execution Engine is unavailable', HttpStatus.SERVICE_UNAVAILABLE);
        },
      );
      this.logger.log('Stopped candle stream');
    } catch (err) {
      this.logger.error(`Failed to stop candle stream: ${err.message}`);
    }
  }

  /**
   * Backfill 1m candles from Deriv API in time-chunked requests.
   */
  private async backfill1mChunked(
    instrumentSymbol: string,
    derivSymbol: string,
    engineBaseUrl: string,
    startDate: Date,
    endDate: Date,
  ): Promise<void> {
    this.logger.log(
      `Backfilling 1m for ${instrumentSymbol} (deriv: ${derivSymbol}) [${startDate.toISOString().slice(0, 10)} → ${endDate.toISOString().slice(0, 10)}]`,
    );

    let totalBackfilled = 0;
    let chunkEnd = new Date(endDate);

    while (chunkEnd > startDate) {
      const chunkStart = new Date(chunkEnd);
      chunkStart.setDate(chunkStart.getDate() - CHUNK_DAYS_1M);
      if (chunkStart < startDate) chunkStart.setTime(startDate.getTime());

      try {
        const response = await this.circuitBreaker.execute(
          () => firstValueFrom(
            this.httpService.post(
              `${engineBaseUrl}/api/candles/historical`,
              {
                broker_symbol: derivSymbol,
                timeframe: '1m',
                start_date: chunkStart.toISOString(),
                end_date: chunkEnd.toISOString(),
              },
              { timeout: 300_000 },
            ),
          ),
          () => {
            throw new HttpException('Execution Engine is unavailable', HttpStatus.SERVICE_UNAVAILABLE);
          },
        );

        const candles = response.data;
        if (Array.isArray(candles) && candles.length > 0) {
          const BATCH_SIZE = 500;
          for (let i = 0; i < candles.length; i += BATCH_SIZE) {
            const batch = candles.slice(i, i + BATCH_SIZE);
            await Promise.all(
              batch.map((c) => {
                const ts = new Date(c.timestamp);
                ts.setSeconds(0, 0);
                return this.marketDataService.upsertCandle({
                  instrument: instrumentSymbol,
                  timeframe: '1m',
                  open: c.open,
                  high: c.high,
                  low: c.low,
                  close: c.close,
                  volume: c.volume || 0,
                  timestamp: ts,
                  completed: true,
                }, true);
              }),
            );
          }
          totalBackfilled += candles.length;
          this.logger.log(
            `Chunk: ${candles.length} 1m candles for ${instrumentSymbol} [${chunkStart.toISOString().slice(0, 10)} → ${chunkEnd.toISOString().slice(0, 10)}]`,
          );
        }
      } catch (chunkErr) {
        this.logger.error(
          `1m chunk failed for ${instrumentSymbol} [${chunkStart.toISOString().slice(0, 10)} → ${chunkEnd.toISOString().slice(0, 10)}]: ${chunkErr.message}`,
        );
      }

      chunkEnd = new Date(chunkStart);
    }

    if (totalBackfilled > 0) {
      this.logger.log(
        `Backfilled ${totalBackfilled} total 1m candles for ${instrumentSymbol}`,
      );
    }
  }

  /**
   * Re-aggregate higher timeframes (5m, 15m, 30m, 1h, 4h, 1d) from persisted 1m candles.
   */
  private async reaggregateHigherTimeframes(
    instrumentSymbol: string,
    startDate: Date,
    endDate: Date,
  ): Promise<void> {
    const rangeCandles = await this.candleRepo
      .createQueryBuilder('c')
      .where('c.instrument = :instrument', { instrument: instrumentSymbol })
      .andWhere('c.timeframe = :timeframe', { timeframe: '1m' })
      .andWhere('c.timestamp >= :start', { start: startDate })
      .andWhere('c.timestamp <= :end', { end: endDate })
      .orderBy('c.timestamp', 'ASC')
      .getMany();

    if (rangeCandles.length === 0) {
      this.logger.debug(`No 1m candles to aggregate for ${instrumentSymbol}`);
      return;
    }

    // Aggregate all timeframes in parallel — each TF builds its own bucket
    // map from the shared 1m data, then writes independently.
    await Promise.all(
      AGGREGATION_TARGETS.map(async ([tf, minutes]) => {
        const buckets = new Map<
          number,
          { open: number; high: number; low: number; close: number; volume: number; bucketStart: Date }
        >();

        for (const candle of rangeCandles) {
          const bucketStart = this.floorToInterval(candle.timestamp, minutes);
          const key = bucketStart.getTime();
          const existing = buckets.get(key);

          if (existing) {
            existing.high = Math.max(existing.high, candle.high);
            existing.low = Math.min(existing.low, candle.low);
            existing.close = candle.close;
            existing.volume += candle.volume;
          } else {
            buckets.set(key, {
              open: candle.open,
              high: candle.high,
              low: candle.low,
              close: candle.close,
              volume: candle.volume,
              bucketStart,
            });
          }
        }

        let count = 0;
        const BATCH_SIZE = 200;
        const entries = Array.from(buckets.values());

        for (let i = 0; i < entries.length; i += BATCH_SIZE) {
          const batch = entries.slice(i, i + BATCH_SIZE);
          await Promise.all(
            batch.map((b) =>
              this.marketDataService.upsertCandle({
                instrument: instrumentSymbol,
                timeframe: tf,
                open: b.open,
                high: b.high,
                low: b.low,
                close: b.close,
                volume: b.volume,
                timestamp: b.bucketStart,
                completed: true,
              }, true),
            ),
          );
          count += batch.length;
        }

        if (count > 0) {
          this.logger.log(
            `Re-aggregated ${count} ${tf} candles for ${instrumentSymbol}`,
          );
        }
      }),
    );
  }

  private floorToInterval(date: Date, intervalMinutes: number): Date {
    if (intervalMinutes >= 1440) {
      const d = new Date(date);
      d.setUTCHours(0, 0, 0, 0);
      return d;
    }
    const ms = intervalMinutes * 60 * 1000;
    return new Date(Math.floor(date.getTime() / ms) * ms);
  }

  private async hasFreshData(
    instrument: string,
    timeframe: string,
  ): Promise<boolean> {
    const twentyFourHoursAgo = new Date(Date.now() - 24 * 60 * 60 * 1000);

    const result = await this.candleRepo
      .createQueryBuilder('c')
      .select('MAX(c.timestamp)', 'maxTs')
      .addSelect('COUNT(*)', 'cnt')
      .where('c.instrument = :instrument', { instrument })
      .andWhere('c.timeframe = :timeframe', { timeframe })
      .getRawOne();

    if (!result?.maxTs) return false;

    const isFresh = new Date(result.maxTs) > twentyFourHoursAgo;
    const count = parseInt(result.cnt, 10) || 0;

    return isFresh && count >= 100;
  }
}
