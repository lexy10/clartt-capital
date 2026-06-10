import {
  Controller,
  Post,
  Get,
  Body,
  UseGuards,
  Request,
} from '@nestjs/common';
import { AdminService } from './admin.service';
import { KillSwitchDto } from './dto/kill-switch.dto';
import { JwtAuthGuard } from '../../common/guards/jwt-auth.guard';
import { RolesGuard } from '../../common/guards/roles.guard';
import { Roles } from '../../common/decorators/roles.decorator';

@Controller('admin')
@UseGuards(JwtAuthGuard, RolesGuard)
@Roles('admin')
export class AdminController {
  constructor(private readonly adminService: AdminService) {}

  @Post('kill-switch')
  async toggleKillSwitch(@Request() req: any, @Body() dto: KillSwitchDto) {
    if (dto.active) {
      return this.adminService.activateKillSwitch(req.user.id, dto.mode ?? 'soft');
    }
    return this.adminService.deactivateKillSwitch(req.user.id);
  }

  @Get('status')
  async getStatus() {
    return this.adminService.getStatus();
  }
}
