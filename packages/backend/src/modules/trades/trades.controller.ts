import {
  Controller,
  Get,
  Param,
  Query,
  UseGuards,
  Request,
  ParseUUIDPipe,
} from '@nestjs/common';
import { TradesService } from './trades.service';
import { JwtAuthGuard } from '../../common/guards/jwt-auth.guard';

@Controller('trades')
@UseGuards(JwtAuthGuard)
export class TradesController {
  constructor(private readonly tradesService: TradesService) {}

  @Get()
  findAll(
    @Request() req: any,
    @Query('limit') limit?: string,
    @Query('offset') offset?: string,
  ) {
    return this.tradesService.findAll(req.user.id, {
      limit: limit ? parseInt(limit, 10) : undefined,
      offset: offset ? parseInt(offset, 10) : undefined,
    });
  }

  @Get(':id')
  findById(
    @Request() req: any,
    @Param('id', ParseUUIDPipe) id: string,
  ) {
    return this.tradesService.findById(req.user.id, id);
  }
}
