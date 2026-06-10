import { Module, OnModuleInit, Logger } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { HttpModule } from '@nestjs/axios';
import { AutopilotState } from './entities/autopilot-state.entity';
import { TradingAccount } from '../trades/entities/trading-account.entity';
import { Position } from '../trades/entities/position.entity';
import { AutopilotController, MasterAutopilotController, InternalAutopilotController } from './autopilot.controller';
import { AutopilotService } from './autopilot.service';
import { GatewayModule } from '../gateway/gateway.module';
import { EventsModule } from '../events/events.module';

@Module({
  imports: [
    TypeOrmModule.forFeature([AutopilotState, TradingAccount, Position]),
    GatewayModule,
    HttpModule,
    EventsModule,
  ],
  controllers: [AutopilotController, MasterAutopilotController, InternalAutopilotController],
  providers: [AutopilotService],
  exports: [AutopilotService],
})
export class AutopilotModule implements OnModuleInit {
  private readonly logger = new Logger(AutopilotModule.name);

  constructor(private readonly autopilotService: AutopilotService) {}

  async onModuleInit(): Promise<void> {
    this.logger.log('Syncing autopilot states to Redis on startup...');
    await this.autopilotService.syncStatesToRedis();
  }
}
