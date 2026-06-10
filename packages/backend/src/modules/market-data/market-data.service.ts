import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository, Between, LessThanOrEqual, MoreThanOrEqual } from 'typeorm';
import { Candle } from './entities/candle.entity';
import { InstrumentsService } from '../instruments/instruments.service';
import { AccountInstrument } from '../instruments/entities/account-instrument.entity';

@Injectable()
export class MarketDataService {
  constructor(
    @InjectRepository(Candle)
    private readonly candleRepo: Repository<Candle>,
    @InjectRepository(AccountInstrument)
    private readonly accountInstrumentRepo: Repository<AccountInstrument>,
    private readonly instrumentsService: InstrumentsService,
  ) {}

  async getCandles(
    instrument: string,
    timeframe: string,
    count: number = 100,
  ): Promise<Candle[]> {
    // Fetch candles ordered newest-first, then reverse for chronological order
    const candles = await this.candleRepo.find({
      where: { instrument, timeframe },
      order: { timestamp: 'DESC' },
      take: count,
    });

    return candles.slice(0, count).reverse();
  }

  async getCandlesByDateRange(
    instrument: string,
    timeframe: string,
    startDate: string,
    endDate: string,
  ): Promise<Candle[]> {
    return this.candleRepo.find({
      where: {
        instrument,
        timeframe,
        timestamp: Between(new Date(startDate), new Date(endDate)),
      },
      order: { timestamp: 'ASC' },
    });
  }

  async getInstruments(): Promise<
    { symbol: string; displayName: string; type: string }[]
  > {
    const instruments = await this.instrumentsService.findAllActive();
    return instruments.map((i) => ({
      symbol: i.symbol,
      displayName: i.displayName,
      type: i.type,
    }));
  }

  async getInstrumentsWithSpecs(): Promise<
    {
      symbol: string;
      displayName: string;
      type: string;
      pipSize: number;
      pipValue: number;
      contractSize: number;
      minLot: number;
      lotStep: number;
      leverage: number;
    }[]
  > {
    const instruments = await this.instrumentsService.findAllActive();
    return instruments.map((i) => ({
      symbol: i.symbol,
      displayName: i.displayName,
      type: i.type,
      pipSize: Number(i.pipSize),
      pipValue: Number(i.pipValue),
      contractSize: Number(i.contractSize),
      minLot: Number(i.minLot),
      lotStep: Number(i.lotStep),
      leverage: i.leverage,
    }));
  }

  /**
   * Upsert a candle.
   * - If `force` is true, always overwrite (used by backfill/reaggregation).
   * - Otherwise, skip the update when the existing row is already completed.
   */
  async upsertCandle(
    candle: Partial<Candle>,
    force = false,
  ): Promise<void> {
    if (force) {
      // Backfill path: always overwrite, mark completed
      await this.candleRepo
        .createQueryBuilder()
        .insert()
        .into(Candle)
        .values({ ...candle, completed: candle.completed ?? true })
        .orUpdate(
          ['open', 'high', 'low', 'close', 'volume', 'completed'],
          ['instrument', 'timeframe', 'timestamp'],
        )
        .execute();
    } else {
      // Live tick path: only update if not already completed
      await this.candleRepo.query(
        `INSERT INTO candles (instrument, timeframe, "open", high, low, close, volume, timestamp, completed)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
         ON CONFLICT (instrument, timeframe, timestamp)
         DO UPDATE SET
           "open" = CASE WHEN candles.completed = true THEN candles."open" ELSE EXCLUDED."open" END,
           high = CASE WHEN candles.completed = true THEN candles.high ELSE EXCLUDED.high END,
           low = CASE WHEN candles.completed = true THEN candles.low ELSE EXCLUDED.low END,
           close = CASE WHEN candles.completed = true THEN candles.close ELSE EXCLUDED.close END,
           volume = CASE WHEN candles.completed = true THEN candles.volume ELSE EXCLUDED.volume END,
           completed = CASE WHEN candles.completed = true THEN true ELSE EXCLUDED.completed END`,
        [
          candle.instrument,
          candle.timeframe,
          candle.open,
          candle.high,
          candle.low,
          candle.close,
          candle.volume ?? 0,
          candle.timestamp,
          candle.completed ?? false,
        ],
      );
    }
  }

  async resolveBrokerSymbol(instrumentSymbol: string): Promise<string> {
    const mapping = await this.accountInstrumentRepo
      .createQueryBuilder('ai')
      .innerJoinAndSelect('ai.instrument', 'instrument')
      .where('instrument.symbol = :symbol', { symbol: instrumentSymbol })
      .getOne();

    if (mapping) {
      return mapping.brokerSymbol;
    }

    return instrumentSymbol;
  }

  /** Mark a specific candle as completed (used when bucket rolls over). */
  async markCompleted(
    instrument: string,
    timeframe: string,
    timestamp: Date,
  ): Promise<void> {
    await this.candleRepo.query(
      `UPDATE candles SET completed = true WHERE instrument = $1 AND timeframe = $2 AND timestamp = $3`,
      [instrument, timeframe, timestamp],
    );
  }
}
