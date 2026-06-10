import { Module } from '@nestjs/common';
import { HttpModule } from '@nestjs/axios';
import { AgentsController } from './agents.controller';
import { AgentsService } from './agents.service';
import { AgentsGateway } from './agents.gateway';

@Module({
  imports: [HttpModule],
  controllers: [AgentsController],
  providers: [AgentsGateway, AgentsService],
  exports: [AgentsGateway],
})
export class AgentsModule {}
