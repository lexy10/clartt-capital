import { Controller, Get, Query } from '@nestjs/common';
import { MarketDataService } from './market-data.service';
import { InstrumentsService } from '../instruments/instruments.service';
import { StrategiesService } from '../strategies/strategies.service';

/**
 * Internal endpoints for service-to-service communication.
 * No JWT guard — only accessible within the Docker network.
 */
@Controller('internal')
export class InternalMarketDataController {
  constructor(
    private readonly marketDataService: MarketDataService,
    private readonly instrumentsService: InstrumentsService,
    private readonly strategiesService: StrategiesService,
  ) {}

  @Get('candles')
  getCandles(
    @Query('instrument') instrument: string,
    @Query('timeframe') timeframe: string,
    @Query('count') count?: string,
    @Query('start_date') startDate?: string,
    @Query('end_date') endDate?: string,
  ) {
    if (startDate && endDate) {
      return this.marketDataService.getCandlesByDateRange(
        instrument,
        timeframe,
        startDate,
        endDate,
      );
    }
    return this.marketDataService.getCandles(
      instrument,
      timeframe,
      count ? parseInt(count, 10) : 500,
    );
  }

  @Get('instruments')
  getInstruments() {
    return this.marketDataService.getInstruments();
  }

  @Get('instruments/specs')
  getInstrumentSpecs() {
    return this.marketDataService.getInstrumentsWithSpecs();
  }

  @Get('strategies')
  getStrategies() {
    return this.strategiesService.findAll();
  }
}
