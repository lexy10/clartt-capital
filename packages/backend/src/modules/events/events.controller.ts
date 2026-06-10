import {
  Controller,
  Get,
  Param,
  Query,
  UseGuards,
  BadRequestException,
} from '@nestjs/common';
import { JwtAuthGuard } from '../../common/guards/jwt-auth.guard';
import { EventQueryService } from './event-query.service';
import { StateReconstructorService } from './state-reconstructor.service';
import { EventQueryDto } from './dto/event-query.dto';

@Controller('events')
@UseGuards(JwtAuthGuard)
export class EventsController {
  constructor(
    private readonly eventQueryService: EventQueryService,
    private readonly stateReconstructorService: StateReconstructorService,
  ) {}

  @Get()
  findEvents(@Query() query: EventQueryDto) {
    return this.eventQueryService.findEvents(query);
  }

  @Get('aggregates/:aggregateId')
  findByAggregate(@Param('aggregateId') aggregateId: string) {
    return this.eventQueryService.findByAggregate(aggregateId);
  }

  @Get('reconstruct')
  reconstruct(
    @Query('aggregate_id') aggregateId: string,
    @Query('timestamp') timestamp: string,
  ) {
    if (!aggregateId) {
      throw new BadRequestException('aggregate_id query parameter is required');
    }
    if (!timestamp) {
      throw new BadRequestException('timestamp query parameter is required');
    }
    return this.stateReconstructorService.reconstruct(aggregateId, timestamp);
  }
}
