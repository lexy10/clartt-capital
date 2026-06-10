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

@Controller('strategies')
export class StrategiesController {
  constructor(private readonly strategiesService: StrategiesService) {}

  @Get()
  findAll() {
    return this.strategiesService.findAll();
  }

  @Get('algorithms')
  getAlgorithms() {
    return this.strategiesService.getAlgorithms();
  }

  @Get('algorithms/:name/source')
  getAlgorithmSource(@Param('name') name: string) {
    return this.strategiesService.getAlgorithmSource(name);
  }

  @Post('algorithms/upload')
  @UseGuards(JwtAuthGuard)
  @UseInterceptors(FileInterceptor('file'))
  uploadAlgorithm(@UploadedFile() file: any) {
    return this.strategiesService.uploadAlgorithm(file);
  }

  @Patch('algorithms/:name/source')
  @UseGuards(JwtAuthGuard)
  updateAlgorithmSource(
    @Param('name') name: string,
    @Body('source') source: string,
  ) {
    return this.strategiesService.updateAlgorithmSource(name, source);
  }

  @Delete('algorithms/:name')
  @UseGuards(JwtAuthGuard)
  @HttpCode(204)
  deleteAlgorithm(@Param('name') name: string) {
    return this.strategiesService.deleteAlgorithm(name);
  }

  @Post()
  @UseGuards(JwtAuthGuard)
  create(@Request() req: any, @Body() dto: CreateStrategyDto) {
    return this.strategiesService.create(dto, req.user.id);
  }

  @Post('backtest')
  @UseGuards(JwtAuthGuard)
  runBacktest(@Request() req: any, @Body() config: BacktestConfigDto) {
    return this.strategiesService.runBacktest(req.user.id, config);
  }

  @Patch(':id')
  @UseGuards(JwtAuthGuard)
  update(
    @Param('id', ParseUUIDPipe) id: string,
    @Body() dto: UpdateStrategyDto,
  ) {
    return this.strategiesService.update(id, dto);
  }

  @Delete(':id')
  @UseGuards(JwtAuthGuard)
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
