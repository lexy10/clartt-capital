import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { ScheduleModule } from '@nestjs/schedule';
import { TradingEvent } from './entities/trading-event.entity';
import { EventPublisherService } from './event-publisher.service';
import { EventIngestionService } from './event-ingestion.service';
import { EventQueryService } from './event-query.service';
import { StateReconstructorService } from './state-reconstructor.service';
import { EventStoreMetrics } from './event-store-metrics';
import { ArchivalService } from './archival.service';
import { EventsController } from './events.controller';
import { GatewayModule } from '../gateway/gateway.module';

@Module({
  imports: [
    TypeOrmModule.forFeature([TradingEvent]),
    ScheduleModule.forRoot(),
    GatewayModule,
  ],
  controllers: [EventsController],
  providers: [
    EventPublisherService,
    EventIngestionService,
    EventQueryService,
    StateReconstructorService,
    EventStoreMetrics,
    ArchivalService,
  ],
  exports: [EventPublisherService],
})
export class EventsModule {}
