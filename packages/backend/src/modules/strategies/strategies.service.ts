import { Injectable, Logger, NotFoundException, BadRequestException, HttpException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { HttpService } from '@nestjs/axios';
import { firstValueFrom } from 'rxjs';
import { Strategy } from './entities/strategy.entity';
import { BacktestResult } from './entities/backtest-result.entity';
import { BacktestTrade } from './entities/backtest-trade.entity';
import { BacktestConfigDto } from './dto/backtest-config.dto';
import { CreateStrategyDto } from './dto/create-strategy.dto';
import { UpdateStrategyDto } from './dto/update-strategy.dto';
import { BacktestStreamPublisher } from './backtest-stream.publisher';
import { InstrumentsService } from '../instruments/instruments.service';

export interface AlgorithmInfo {
  name: string;
  description: string;
  default_params: Record<string, unknown>;
  param_schema: Record<string, unknown>;
}

@Injectable()
export class StrategiesService {
  private readonly logger = new Logger(StrategiesService.name);
  private readonly strategyEngineUrl: string;

  constructor(
    @InjectRepository(Strategy)
    private readonly strategiesRepository: Repository<Strategy>,
    @InjectRepository(BacktestResult)
    private readonly backtestResultsRepository: Repository<BacktestResult>,
    @InjectRepository(BacktestTrade)
    private readonly backtestTradesRepository: Repository<BacktestTrade>,
    private readonly backtestStreamPublisher: BacktestStreamPublisher,
    private readonly instrumentsService: InstrumentsService,
    private readonly httpService: HttpService,
  ) {
    this.strategyEngineUrl = process.env.STRATEGY_ENGINE_API_URL || 'http://strategy-engine:8003';
  }

  async findAll() {
    return this.strategiesRepository.find({
      order: { createdAt: 'DESC' },
    });
  }

  /** Trader-safe strategy list: keeps identity + which instruments/timeframes
   *  it trades, but strips the tuned "sauce" (algorithm_params, risk_settings,
   *  exit_rules). Traders can still verify performance by running a backtest. */
  async findAllPublic() {
    const strategies = await this.findAll();
    return strategies.map((s) => {
      const cfg = (s.config ?? {}) as Record<string, unknown>;
      return {
        id: s.id,
        name: s.name,
        algorithm: s.algorithm,
        enabled: s.enabled,
        config: {
          instruments: cfg.instruments ?? [],
          entry_timeframe: cfg.entry_timeframe ?? null,
          higher_timeframe: cfg.higher_timeframe ?? null,
          trend_timeframe: cfg.trend_timeframe ?? null,
          mode: cfg.mode ?? null,
          min_confidence_score: cfg.min_confidence_score ?? null,
        },
        createdAt: s.createdAt,
      };
    });
  }

  /** Trader-safe algorithm list: name + description only, no params/source. */
  async getAlgorithmsPublic(): Promise<Array<Pick<AlgorithmInfo, 'name' | 'description'>>> {
    const algos = await this.getAlgorithms();
    return algos.map(({ name, description }) => ({ name, description }));
  }

  async runBacktest(userId: string, config: BacktestConfigDto) {
    const strategy = await this.strategiesRepository.findOne({
      where: { id: config.strategyId },
    });

    if (!strategy) {
      throw new NotFoundException(
        `Strategy with id ${config.strategyId} not found`,
      );
    }

    // Validate instrument (default to strategy's first instrument, then R_75)
    const strategyInstruments = (strategy.config?.instruments ?? []) as string[];
    const instrument = config.instrument || strategyInstruments[0] || 'R_75';
    const isValid = await this.instrumentsService.validateInstrumentSymbol(instrument);
    if (!isValid) {
      throw new BadRequestException(
        `Instrument '${instrument}' is not a registered active instrument`,
      );
    }

    // Look up instrument specs for position sizing
    const instrumentEntity = await this.instrumentsService.findBySymbol(instrument);
    const instrumentSpecs = instrumentEntity ? {
      contract_size: Number(instrumentEntity.contractSize),
      pip_size: Number(instrumentEntity.pipSize),
      pip_value: Number(instrumentEntity.pipValue),
      min_lot: Number(instrumentEntity.minLot),
      lot_step: Number(instrumentEntity.lotStep),
      leverage: Number(instrumentEntity.leverage),
    } : undefined;

    // Create pending backtest result record
    const result = this.backtestResultsRepository.create({
      strategyId: strategy.id,
      userId,
      config: {
        strategyId: config.strategyId,
        instrument,
        timeframe: config.timeframe || strategy.config?.entry_timeframe || '1h',
        parameters: config.parameters ?? {},
        startDate: config.startDate ?? null,
        endDate: config.endDate ?? null,
        strategySnapshot: {
          name: strategy.name,
          algorithm: strategy.algorithm,
          ...strategy.config,
        },
      },
      status: 'pending',
      winRate: null,
      maxDrawdown: null,
      sharpeRatio: null,
      profitFactor: null,
      expectancy: null,
      totalTrades: null,
      tradeResults: null,
    });

    const saved = await this.backtestResultsRepository.save(result);

    // Publish request to Redis stream for async processing
    try {
      // Separate backtest engine params from algorithm param overrides.
      // The `parameters` field from the DTO is a mixed bag — extract known
      // BacktestParams keys and leave the rest as algorithm_params overrides.
      const backtestParamKeys = new Set([
        'initial_capital', 'commission_per_trade', 'slippage', 'spread', 'max_lot_size',
      ]);
      const rawParams = config.parameters ?? {};
      const backtestParams: Record<string, unknown> = {};
      const algorithmParamOverrides: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(rawParams)) {
        if (backtestParamKeys.has(k)) {
          backtestParams[k] = v;
        } else {
          algorithmParamOverrides[k] = v;
        }
      }

      // Merge algorithm param overrides into the strategy config's algorithm_params
      const baseAlgorithmParams = (strategy.config?.algorithm_params as Record<string, unknown>) ?? {};
      const mergedAlgorithmParams = { ...baseAlgorithmParams, ...algorithmParamOverrides };

      await this.backtestStreamPublisher.publishRequest({
        result_id: saved.id,
        strategy_id: strategy.id,
        strategy_config: {
          id: strategy.id,
          name: strategy.name,
          algorithm: strategy.algorithm,
          ...strategy.config,
          algorithm_params: mergedAlgorithmParams,
        },
        instrument,
        timeframe: config.timeframe || (strategy.config?.entry_timeframe as string) || '1h',
        params: backtestParams,
        start_date: config.startDate ?? new Date().toISOString(),
        end_date: config.endDate ?? new Date().toISOString(),
        instrument_specs: instrumentSpecs,
      });
    } catch (error) {
      // Let HttpExceptions (e.g. 429 backpressure) propagate to the controller
      if (error instanceof HttpException) {
        // Clean up the pending record before rejecting
        await this.backtestResultsRepository.remove(saved);
        throw error;
      }
      // If Redis publish fails, mark the record as failed
      saved.status = 'failed';
      saved.errorMessage =
        error instanceof Error
          ? error.message
          : 'Failed to publish backtest request';
      await this.backtestResultsRepository.save(saved);
    }

    return saved;
  }

  async getBacktestResults(strategyId: string) {
    return this.backtestResultsRepository.find({
      where: { strategyId },
      order: { createdAt: 'DESC' },
    });
  }

  async failStaleBacktests(): Promise<number> {
    const result = await this.backtestResultsRepository
      .createQueryBuilder()
      .update()
      .set({
        status: 'failed',
        errorMessage: 'Cancelled: service restarted while backtest was in progress',
      })
      .where('status IN (:...statuses)', { statuses: ['pending', 'running'] })
      .execute();
    return result.affected ?? 0;
  }

  async updateBacktestStatus(
    resultId: string,
    status: string,
    errorMessage?: string,
  ): Promise<void> {
    const record = await this.backtestResultsRepository.findOne({
      where: { id: resultId },
    });

    if (!record) {
      this.logger.warn(
        `BacktestResult not found for status update: ${resultId}`,
      );
      return;
    }

    // Don't overwrite terminal states (completed/failed)
    if (record.status === 'completed' || record.status === 'failed') {
      this.logger.debug(
        `Ignoring status update for ${resultId}: already in terminal state "${record.status}"`,
      );
      return;
    }

    record.status = status;
    if (errorMessage !== undefined) {
      record.errorMessage = errorMessage;
    }

    await this.backtestResultsRepository.save(record);
  }

  async updateBacktestResult(
    resultId: string,
    metrics: {
      winRate?: number | null;
      maxDrawdown?: number | null;
      sharpeRatio?: number | null;
      profitFactor?: number | null;
      expectancy?: number | null;
      totalTrades?: number | null;
      winningTrades?: number | null;
      losingTrades?: number | null;
      grossProfit?: number | null;
      grossLoss?: number | null;
      netProfit?: number | null;
      averageRr?: number | null;
      equityCurve?: number[] | null;
      tradeResults?: Record<string, unknown>[] | null;
    },
  ): Promise<void> {
    const record = await this.backtestResultsRepository.findOne({
      where: { id: resultId },
    });

    if (!record) {
      this.logger.warn(
        `BacktestResult not found for result update: ${resultId}`,
      );
      return;
    }

    // Don't overwrite a record that was already marked failed (e.g. stale cleanup)
    if (record.status === 'failed') {
      this.logger.debug(
        `Ignoring result update for ${resultId}: already in terminal state "failed"`,
      );
      return;
    }

    if (metrics.winRate !== undefined) record.winRate = metrics.winRate != null ? String(metrics.winRate) : null;
    if (metrics.maxDrawdown !== undefined) record.maxDrawdown = metrics.maxDrawdown != null ? String(metrics.maxDrawdown) : null;
    if (metrics.sharpeRatio !== undefined) record.sharpeRatio = metrics.sharpeRatio != null ? String(metrics.sharpeRatio) : null;
    if (metrics.profitFactor !== undefined) record.profitFactor = metrics.profitFactor != null ? String(metrics.profitFactor) : null;
    if (metrics.expectancy !== undefined) record.expectancy = metrics.expectancy != null ? String(metrics.expectancy) : null;
    if (metrics.totalTrades !== undefined) record.totalTrades = metrics.totalTrades ?? null;
    if (metrics.winningTrades !== undefined) record.winningTrades = metrics.winningTrades ?? null;
    if (metrics.losingTrades !== undefined) record.losingTrades = metrics.losingTrades ?? null;
    if (metrics.grossProfit !== undefined) record.grossProfit = metrics.grossProfit != null ? String(metrics.grossProfit) : null;
    if (metrics.grossLoss !== undefined) record.grossLoss = metrics.grossLoss != null ? String(metrics.grossLoss) : null;
    if (metrics.netProfit !== undefined) record.netProfit = metrics.netProfit != null ? String(metrics.netProfit) : null;
    if (metrics.averageRr !== undefined) record.averageRr = metrics.averageRr != null ? String(metrics.averageRr) : null;
    if (metrics.equityCurve !== undefined) record.equityCurve = metrics.equityCurve ?? null;
    if (metrics.tradeResults !== undefined) record.tradeResults = metrics.tradeResults ?? null;

    record.status = 'completed';

    await this.backtestResultsRepository.save(record);
  }

  async saveBacktestTrades(
    resultId: string,
    trades: Array<{
      signal_id: string;
      direction: string;
      entry_price: number;
      exit_price: number;
      stop_loss?: number;
      take_profit?: number;
      initial_stop_loss?: number;
      position_size: number;
      profit_loss: number;
      reward_risk?: number | null;
      entry_time: string;
      exit_time: string;
      balance_before?: number | null;
      balance_after?: number | null;
    }>,
  ): Promise<void> {
    if (!trades.length) return;

    const entities = trades.map((t, index) =>
      this.backtestTradesRepository.create({
        backtestResultId: resultId,
        signalId: t.signal_id,
        direction: t.direction,
        entryPrice: String(t.entry_price),
        exitPrice: String(t.exit_price),
        stopLoss: t.stop_loss != null ? String(t.stop_loss) : null,
        takeProfit: t.take_profit != null ? String(t.take_profit) : null,
        initialStopLoss: t.initial_stop_loss != null ? String(t.initial_stop_loss) : null,
        positionSize: String(t.position_size),
        profitLoss: String(t.profit_loss),
        rewardRisk: t.reward_risk != null ? String(t.reward_risk) : null,
        balanceBefore: t.balance_before != null ? String(t.balance_before) : null,
        balanceAfter: t.balance_after != null ? String(t.balance_after) : null,
        entryTime: new Date(t.entry_time),
        exitTime: new Date(t.exit_time),
        tradeIndex: index,
      }),
    );

    await this.backtestTradesRepository.save(entities);
  }

  async getBacktestTrades(
    resultId: string,
    skip = 0,
    take = 50,
  ): Promise<{ items: BacktestTrade[]; total: number }> {
    const [items, total] = await this.backtestTradesRepository.findAndCount({
      where: { backtestResultId: resultId },
      order: { tradeIndex: 'ASC' },
      skip,
      take,
    });
    return { items, total };
  }

  async update(id: string, dto: UpdateStrategyDto): Promise<Strategy> {
    const strategy = await this.strategiesRepository.findOne({
      where: { id },
    });
    if (!strategy) {
      throw new NotFoundException(`Strategy ${id} not found`);
    }
    if (dto.name !== undefined) strategy.name = dto.name;
    if (dto.algorithm !== undefined) strategy.algorithm = dto.algorithm;
    if (dto.config !== undefined) strategy.config = dto.config;
    if (dto.enabled !== undefined) strategy.enabled = dto.enabled;
    return this.strategiesRepository.save(strategy);
  }

  async create(dto: CreateStrategyDto, userId: string): Promise<Strategy> {
    const strategy = this.strategiesRepository.create({
      name: dto.name,
      algorithm: dto.algorithm ?? 'ict_order_block',
      config: dto.config,
      createdBy: userId,
    });
    return this.strategiesRepository.save(strategy);
  }

  async remove(id: string): Promise<void> {
    const strategy = await this.strategiesRepository.findOne({
      where: { id },
    });
    if (!strategy) {
      throw new NotFoundException(`Strategy ${id} not found`);
    }
    await this.strategiesRepository.remove(strategy);
  }

  async getAlgorithms(): Promise<AlgorithmInfo[]> {
    try {
      const { data } = await firstValueFrom(
        this.httpService.get<AlgorithmInfo[]>(`${this.strategyEngineUrl}/algorithms`),
      );
      return data;
    } catch (error) {
      this.logger.warn('Failed to fetch algorithms from strategy engine, using fallback');
      return [
        {
          name: 'ict_order_block',
          description:
            'ICT/Smart Money Concepts: order blocks, BOS, liquidity sweeps',
          default_params: { structure_lookback: 20 },
          param_schema: {
            type: 'object',
            properties: {
              structure_lookback: {
                type: 'integer',
                minimum: 5,
                maximum: 100,
                description:
                  'Number of candles for structure detection window',
              },
            },
            additionalProperties: false,
          },
        },
      ];
    }
  }

  async getAlgorithmSource(name: string): Promise<{ name: string; source: string; filename: string }> {
    try {
      const { data } = await firstValueFrom(
        this.httpService.get(`${this.strategyEngineUrl}/algorithms/${name}/source`),
      );
      return data;
    } catch (error: any) {
      if (error?.response?.status === 404) {
        throw new NotFoundException(`Algorithm '${name}' not found`);
      }
      throw new BadRequestException('Failed to fetch algorithm source from strategy engine');
    }
  }

  async uploadAlgorithm(file: any): Promise<{ name: string; message: string }> {
    if (!file) {
      throw new BadRequestException('No file provided');
    }
    try {
      const FormData = (await import('form-data')).default;
      const formData = new FormData();
      formData.append('file', file.buffer, { filename: file.originalname, contentType: 'text/x-python' });

      const { data } = await firstValueFrom(
        this.httpService.post(`${this.strategyEngineUrl}/algorithms`, formData, {
          headers: formData.getHeaders(),
        }),
      );
      return data;
    } catch (error: any) {
      const detail = error?.response?.data?.detail || 'Failed to upload algorithm';
      throw new BadRequestException(detail);
    }
  }

  async updateAlgorithmSource(name: string, source: string): Promise<{ name: string; message: string }> {
    try {
      const { data } = await firstValueFrom(
        this.httpService.patch(`${this.strategyEngineUrl}/algorithms/${name}/source`, { source }),
      );
      return data;
    } catch (error: any) {
      if (error?.response?.status === 404) {
        throw new NotFoundException(`Algorithm '${name}' not found`);
      }
      const detail = error?.response?.data?.detail || 'Failed to update algorithm source';
      throw new BadRequestException(detail);
    }
  }

  async deleteAlgorithm(name: string): Promise<void> {
    try {
      await firstValueFrom(
        this.httpService.delete(`${this.strategyEngineUrl}/algorithms/${name}`),
      );
    } catch (error: any) {
      if (error?.response?.status === 404) {
        throw new NotFoundException(`Algorithm '${name}' not found`);
      }
      throw new BadRequestException('Failed to delete algorithm from strategy engine');
    }
  }
}

