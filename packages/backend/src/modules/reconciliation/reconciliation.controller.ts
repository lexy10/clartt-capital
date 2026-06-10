import {
  Controller,
  Get,
  Put,
  Body,
  Param,
  Query,
  UseGuards,
  ParseUUIDPipe,
  NotFoundException,
  Inject,
} from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository, SelectQueryBuilder } from 'typeorm';
import Redis from 'ioredis';

import { JwtAuthGuard } from '../../common/guards/jwt-auth.guard';
import { REDIS_CLIENT } from '../../common/modules/redis.module';
import { ReconciliationReport } from './entities/reconciliation-report.entity';
import { ReconciliationConfig } from './entities/reconciliation-config.entity';
import { ReconciliationConfigService } from './reconciliation-config.service';
import { ReportQueryDto } from './dto/report-query.dto';
import { UpdateConfigDto } from './dto/update-config.dto';

@Controller('reconciliation')
@UseGuards(JwtAuthGuard)
export class ReconciliationController {
  constructor(
    @InjectRepository(ReconciliationReport)
    private readonly reportRepo: Repository<ReconciliationReport>,
    @InjectRepository(ReconciliationConfig)
    private readonly configRepo: Repository<ReconciliationConfig>,
    private readonly configService: ReconciliationConfigService,
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
  ) {}

  @Get('reports')
  async getReports(@Query() query: ReportQueryDto) {
    const { account_id, start_date, end_date, status, page, limit } = query;
    const take = limit ?? 20;
    const skip = ((page ?? 1) - 1) * take;

    const qb: SelectQueryBuilder<ReconciliationReport> = this.reportRepo
      .createQueryBuilder('report')
      .orderBy('report.cycle_timestamp', 'DESC');

    if (account_id) {
      qb.andWhere('report.account_id = :account_id', { account_id });
    }
    if (start_date) {
      qb.andWhere('report.cycle_timestamp >= :start_date', { start_date });
    }
    if (end_date) {
      qb.andWhere('report.cycle_timestamp <= :end_date', { end_date });
    }
    if (status) {
      qb.andWhere('report.status = :status', { status });
    }

    const [data, total] = await qb.skip(skip).take(take).getManyAndCount();

    return {
      data,
      total,
      page: page ?? 1,
      limit: take,
      totalPages: Math.ceil(total / take),
    };
  }

  @Get('reports/:id')
  async getReport(@Param('id', ParseUUIDPipe) id: string) {
    const report = await this.reportRepo.findOne({ where: { id } });
    if (!report) {
      throw new NotFoundException(`Report ${id} not found`);
    }
    return report;
  }

  @Put('config')
  async updateGlobalConfig(@Body() dto: UpdateConfigDto) {
    return this.configService.updateGlobalConfig(dto);
  }

  @Put('config/:accountId')
  async updateAccountConfig(
    @Param('accountId', ParseUUIDPipe) accountId: string,
    @Body() dto: UpdateConfigDto,
  ) {
    return this.configService.updateAccountConfig(accountId, dto);
  }

  @Get('config')
  async getGlobalConfig() {
    const config = await this.configRepo.findOne({
      where: { accountId: null as unknown as string },
    });
    if (!config) {
      // No global config row yet — return effective defaults
      return this.configService.getEffectiveConfig('__no_account__');
    }
    return config;
  }

  @Get('config/:accountId')
  async getAccountConfig(
    @Param('accountId', ParseUUIDPipe) accountId: string,
  ) {
    return this.configService.getEffectiveConfig(accountId);
  }

  @Get('status/:accountId')
  async getAccountStatus(
    @Param('accountId', ParseUUIDPipe) accountId: string,
  ) {
    const stateKey = `reconciliation:state:${accountId}`;
    const state = await this.redis.hgetall(stateKey);

    if (!state || Object.keys(state).length === 0) {
      return {
        accountId,
        lastCycleAt: null,
        lastStatus: null,
        consecutiveFailures: 0,
      };
    }

    return {
      accountId,
      lastCycleAt: state.last_cycle_at ?? null,
      lastStatus: state.last_status ?? null,
      consecutiveFailures: parseInt(state.consecutive_failures || '0', 10),
    };
  }
}
