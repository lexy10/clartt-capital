import { Injectable, NotFoundException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Signal } from './entities/signal.entity';
import { Trade } from '../trades/entities/trade.entity';
import { CreateSignalDto } from './dto/create-signal.dto';

/** Why a signal did or didn't become a position. Derived, not stored. */
export type SignalExecutionStatus =
  | 'executed'
  | 'no_fill'
  | 'paper'
  | 'backtest';

export interface SignalExecution {
  status: SignalExecutionStatus;
  label: string;
  reason: string;
}

/** A signal reaches a broker only in live mode; forward_test/backtest are
 *  generated for analytics but never sent to the execution stream. For a live
 *  signal, the presence of a (filled/partial) trade row tells us it executed —
 *  the backend only persists fills, so "no row" means it was suppressed
 *  downstream (autopilot off, strategy not assigned, or risk-rejected). */
function deriveExecution(mode: string, hasTrade: boolean): SignalExecution {
  if (mode === 'backtest') {
    return { status: 'backtest', label: 'Backtest', reason: 'Backtest signal — not for live trading.' };
  }
  if (mode === 'forward_test') {
    return {
      status: 'paper',
      label: 'Paper',
      reason: 'Forward-test mode — recorded for analytics but not sent to live execution. Set the strategy to Live to trade it.',
    };
  }
  // live
  if (hasTrade) {
    return { status: 'executed', label: 'Executed', reason: 'A position was opened from this signal.' };
  }
  return {
    status: 'no_fill',
    label: 'No fill',
    reason: 'Live signal with no position — autopilot may be off, the strategy may not be assigned to an account, or risk rules rejected it.',
  };
}

@Injectable()
export class SignalsService {
  constructor(
    @InjectRepository(Signal)
    private readonly signalsRepository: Repository<Signal>,
    @InjectRepository(Trade)
    private readonly tradesRepository: Repository<Trade>,
  ) {}

  /** Which of the given signal ids have at least one persisted (filled) trade. */
  private async signalIdsWithTrades(signalIds: string[]): Promise<Set<string>> {
    if (signalIds.length === 0) return new Set();
    const rows = await this.tradesRepository
      .createQueryBuilder('t')
      .select('DISTINCT t.signal_id', 'signalId')
      .where('t.signal_id IN (:...ids)', { ids: signalIds })
      .getRawMany<{ signalId: string }>();
    return new Set(rows.map((r) => r.signalId));
  }

  /** Attach strategyName + execution status to a signal entity. */
  private present(signal: Signal, executed: boolean) {
    const { strategy, ...rest } = signal;
    return {
      ...rest,
      strategyName: strategy?.name ?? null,
      execution: deriveExecution(signal.mode, executed),
    };
  }

  async findAll(query: { limit?: number; offset?: number }) {
    const limit = query.limit ?? 50;
    const offset = query.offset ?? 0;

    const [rows, total] = await this.signalsRepository.findAndCount({
      order: { createdAt: 'DESC' },
      take: limit,
      skip: offset,
      relations: { strategy: true },
    });

    const withTrades = await this.signalIdsWithTrades(rows.map((r) => r.id));
    const data = rows.map((r) => this.present(r, withTrades.has(r.id)));

    return { data, total, limit, offset };
  }

  async findById(id: string) {
    const signal = await this.signalsRepository.findOne({
      where: { id },
      relations: { strategy: true },
    });
    if (!signal) {
      throw new NotFoundException(`Signal with id ${id} not found`);
    }
    const withTrades = await this.signalIdsWithTrades([signal.id]);
    return this.present(signal, withTrades.has(signal.id));
  }

  async create(dto: CreateSignalDto): Promise<Signal> {
    const signal = this.signalsRepository.create({
      instrument: dto.instrument,
      direction: dto.direction,
      entryPrice: String(dto.entryPrice),
      stopLoss: String(dto.stopLoss),
      takeProfit: String(dto.takeProfit),
      positionSize: String(dto.positionSize),
      confidenceScore: String(dto.confidenceScore),
      timeframe: dto.timeframe,
      orderBlockId: dto.orderBlockId ?? null,
      strategyId: dto.strategyId ?? null,
      mode: dto.mode,
      metadata: dto.metadata ?? null,
    });
    return this.signalsRepository.save(signal);
  }
}
