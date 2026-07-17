import {
  Controller,
  Put,
  Get,
  Body,
  Param,
  UseGuards,
  Request,
} from '@nestjs/common';
import { AutopilotService } from './autopilot.service';
import { SetAutopilotDto } from './dto/set-autopilot.dto';
import { JwtAuthGuard } from '../../common/guards/jwt-auth.guard';

@Controller('trading-accounts')
@UseGuards(JwtAuthGuard)
export class AutopilotController {
  constructor(private readonly autopilotService: AutopilotService) {}

  @Put(':id/autopilot')
  async setAutopilotState(
    @Param('id') id: string,
    @Body() dto: SetAutopilotDto,
    @Request() req: any,
  ) {
    return this.autopilotService.setAutopilotState(
      id,
      dto.enabled,
      req.user.id,
    );
  }

  @Get(':id/autopilot')
  async getAutopilotState(@Param('id') id: string) {
    return this.autopilotService.getAutopilotState(id);
  }
}

@Controller('autopilot')
@UseGuards(JwtAuthGuard)
export class MasterAutopilotController {
  constructor(private readonly autopilotService: AutopilotService) {}

  @Get('master')
  getMaster() {
    return this.autopilotService.getMasterAutopilot();
  }

  @Put('master')
  setMaster(@Body() dto: SetAutopilotDto) {
    return this.autopilotService.setMasterAutopilot(dto.enabled);
  }
}

/** Internal endpoint — no JWT guard, Docker-network only. */
@Controller('internal/autopilot')
export class InternalAutopilotController {
  constructor(private readonly autopilotService: AutopilotService) {}

  @Get('master')
  getMaster() {
    return this.autopilotService.getMasterAutopilot();
  }

  /** Every autopilot-enabled, active account with the fields the execution
   *  engine needs to (re)spawn its worker. Used for startup reconciliation so
   *  trading survives an engine restart. Returns decrypted Deriv tokens — this
   *  MUST stay Docker-network only (blocked from the public proxy). */
  @Get('active-workers')
  activeWorkers() {
    return this.autopilotService.listActiveWorkerRequests();
  }
}
