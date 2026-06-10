import { Controller, Get, Query, UseGuards, Request } from '@nestjs/common';
import { PortfoliosService } from './portfolios.service';
import { PaginationDto } from './dto/pagination.dto';
import { JwtAuthGuard } from '../../common/guards/jwt-auth.guard';

@Controller('portfolios')
@UseGuards(JwtAuthGuard)
export class PortfoliosController {
  constructor(private readonly portfoliosService: PortfoliosService) {}

  @Get('summary')
  getSummary(@Request() req: any) {
    return this.portfoliosService.getSummary(req.user.id);
  }

  @Get('positions')
  getPositions(@Request() req: any) {
    return this.portfoliosService.getPositions(req.user.id);
  }

  @Get('history')
  getHistory(@Request() req: any, @Query() pagination: PaginationDto) {
    return this.portfoliosService.getHistory(
      req.user.id,
      pagination.page,
      pagination.limit,
    );
  }
}
