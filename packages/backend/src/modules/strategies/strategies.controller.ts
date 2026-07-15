import {
  Controller,
  Get,
  Post,
  Patch,
  Delete,
  Body,
  Param,
  Query,
  UseGuards,
  Request,
  ParseUUIDPipe,
  HttpCode,
  UseInterceptors,
  UploadedFile,
} from '@nestjs/common';
import { FileInterceptor } from '@nestjs/platform-express';
import { StrategiesService } from './strategies.service';
import { BacktestConfigDto } from './dto/backtest-config.dto';
import { CreateStrategyDto } from './dto/create-strategy.dto';
import { UpdateStrategyDto } from './dto/update-strategy.dto';
import { JwtAuthGuard } from '../../common/guards/jwt-auth.guard';
import { RolesGuard } from '../../common/guards/roles.guard';
import { Roles } from '../../common/decorators/roles.decorator';

// Reads + backtests are open to any authenticated user (traders view and can
// test). Editing the strategy catalogue and algorithms requires admin+.
function isAdminOrAbove(req: any): boolean {
  const role = req?.user?.role;
  return role === 'admin' || role === 'superadmin';
}

@Controller('strategies')
export class StrategiesController {
  constructor(private readonly strategiesService: StrategiesService) {}

  @Get()
  @UseGuards(JwtAuthGuard)
  findAll(@Request() req: any) {
    return isAdminOrAbove(req) ? this.strategiesService.findAll() : this.strategiesService.findAllPublic();
  }

  @Get('algorithms')
  @UseGuards(JwtAuthGuard)
  getAlgorithms(@Request() req: any) {
    return isAdminOrAbove(req) ? this.strategiesService.getAlgorithms() : this.strategiesService.getAlgorithmsPublic();
  }

  // Algorithm source is the actual logic — admin+ only. Traders never see it.
  @Get('algorithms/:name/source')
  @UseGuards(JwtAuthGuard, RolesGuard)
  @Roles('admin')
  getAlgorithmSource(@Param('name') name: string) {
    return this.strategiesService.getAlgorithmSource(name);
  }

  @Post('algorithms/upload')
  @UseGuards(JwtAuthGuard, RolesGuard)
  @Roles('admin')
  @UseInterceptors(FileInterceptor('file'))
  uploadAlgorithm(@UploadedFile() file: any) {
    return this.strategiesService.uploadAlgorithm(file);
  }

  @Patch('algorithms/:name/source')
  @UseGuards(JwtAuthGuard, RolesGuard)
  @Roles('admin')
  updateAlgorithmSource(
    @Param('name') name: string,
    @Body('source') source: string,
  ) {
    return this.strategiesService.updateAlgorithmSource(name, source);
  }

  @Delete('algorithms/:name')
  @UseGuards(JwtAuthGuard, RolesGuard)
  @Roles('admin')
  @HttpCode(204)
  deleteAlgorithm(@Param('name') name: string) {
    return this.strategiesService.deleteAlgorithm(name);
  }

  @Post()
  @UseGuards(JwtAuthGuard, RolesGuard)
  @Roles('admin')
  create(@Request() req: any, @Body() dto: CreateStrategyDto) {
    return this.strategiesService.create(dto, req.user.id);
  }

  @Post('backtest')
  @UseGuards(JwtAuthGuard)
  runBacktest(@Request() req: any, @Body() config: BacktestConfigDto) {
    return this.strategiesService.runBacktest(req.user.id, config);
  }

  @Patch(':id')
  @UseGuards(JwtAuthGuard, RolesGuard)
  @Roles('admin')
  update(
    @Param('id', ParseUUIDPipe) id: string,
    @Body() dto: UpdateStrategyDto,
  ) {
    return this.strategiesService.update(id, dto);
  }

  @Delete(':id')
  @UseGuards(JwtAuthGuard, RolesGuard)
  @Roles('admin')
  @HttpCode(204)
  remove(@Param('id', ParseUUIDPipe) id: string) {
    return this.strategiesService.remove(id);
  }

  @Get(':id/backtest-results')
  getBacktestResults(@Param('id', ParseUUIDPipe) id: string) {
    return this.strategiesService.getBacktestResults(id);
  }

  @Get('backtest-results/:resultId/trades')
  getBacktestTrades(
    @Param('resultId', ParseUUIDPipe) resultId: string,
    @Query('skip') skip?: string,
    @Query('take') take?: string,
  ) {
    return this.strategiesService.getBacktestTrades(
      resultId,
      skip ? parseInt(skip, 10) : 0,
      take ? parseInt(take, 10) : 50,
    );
  }
}
