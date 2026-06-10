import { Controller, Get, Param, Post, Query, UseGuards } from '@nestjs/common';
import { MarketDataService } from './market-data.service';
import { BackfillService } from './backfill.service';
import { JwtAuthGuard } from '../../common/guards/jwt-auth.guard';
import { CandleQueryDto } from './dto/candle-query.dto';

@Controller('market-data')
@UseGuards(JwtAuthGuard)
export class MarketDataController {
  constructor(
    private readonly marketDataService: MarketDataService,
    private readonly backfillService: BackfillService,
  ) {}

  @Get('candles')
  getCandles(@Query() query: CandleQueryDto) {
    if (query.startDate && query.endDate) {
      return this.marketDataService.getCandlesByDateRange(
        query.instrument,
        query.timeframe,
        query.startDate,
        query.endDate,
      );
    }
    const count = query.count ? parseInt(query.count, 10) : 100;
    return this.marketDataService.getCandles(
      query.instrument,
      query.timeframe,
      count,
    );
  }

  @Get('instruments')
  getInstruments() {
    return this.marketDataService.getInstruments();
  }

  @Get('broker-symbol/:instrumentSymbol')
  async getBrokerSymbol(@Param('instrumentSymbol') instrumentSymbol: string) {
    const brokerSymbol = await this.marketDataService.resolveBrokerSymbol(instrumentSymbol);
    return { brokerSymbol };
  }

  @Post('backfill')
  async triggerBackfill() {
    // Fire and forget — backfill runs in background
    this.backfillService.triggerBackfill(false).catch(() => {});
    return { status: 'backfill_started' };
  }

  @Post('backfill/force')
  async triggerForceBackfill() {
    // Force backfill even if fresh data exists
    this.backfillService.triggerBackfill(true).catch(() => {});
    return { status: 'force_backfill_started' };
  }

  @Post('stream/stop')
  async stopStream() {
    await this.backfillService.stopStream();
    return { status: 'stream_stopped' };
  }
}
