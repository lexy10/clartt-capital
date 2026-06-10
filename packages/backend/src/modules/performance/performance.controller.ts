import {
  Controller,
  Get,
  Param,
  Query,
  UseGuards,
  Request,
  BadRequestException,
  ParseUUIDPipe,
} from '@nestjs/common';
import { JwtAuthGuard } from '../../common/guards/jwt-auth.guard';
import { PerformanceService } from './performance.service';
import { TimePeriod } from './performance.enums';

const VALID_PERIODS = Object.values(TimePeriod);

@Controller('performance')
@UseGuards(JwtAuthGuard)
export class PerformanceController {
  constructor(private readonly performanceService: PerformanceService) {}

  private validatePeriod(period: string): TimePeriod {
    if (!period || !VALID_PERIODS.includes(period as TimePeriod)) {
      throw new BadRequestException(
        'Invalid period. Must be one of: today, this_week, this_month, all_time',
      );
    }
    return period as TimePeriod;
  }

  @Get('overview')
  getOverview(@Request() req: any, @Query('period') period: string) {
    const validPeriod = this.validatePeriod(period);
    return this.performanceService.getOverview(req.user.id, validPeriod);
  }

  @Get('accounts')
  getAccountPerformance(@Request() req: any, @Query('period') period: string) {
    const validPeriod = this.validatePeriod(period);
    return this.performanceService.getAccountPerformance(req.user.id, validPeriod);
  }

  @Get('accounts/:accountId/trades')
  getAccountTrades(
    @Request() req: any,
    @Param('accountId', ParseUUIDPipe) accountId: string,
    @Query('period') period: string,
  ) {
    const validPeriod = this.validatePeriod(period);
    return this.performanceService.getAccountTrades(req.user.id, accountId, validPeriod);
  }

  @Get('activity')
  getRecentActivity(
    @Request() req: any,
    @Query('limit') limit?: string,
  ) {
    const parsedLimit = limit ? Math.min(parseInt(limit, 10), 50) : 10;
    return this.performanceService.getRecentActivity(req.user.id, parsedLimit);
  }

  @Get('strategies')
  getStrategyPerformance(@Request() req: any, @Query('period') period: string) {
    const validPeriod = this.validatePeriod(period);
    return this.performanceService.getStrategyPerformance(req.user.id, validPeriod);
  }


}
