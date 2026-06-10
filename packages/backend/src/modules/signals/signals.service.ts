import { Injectable, NotFoundException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Signal } from './entities/signal.entity';
import { CreateSignalDto } from './dto/create-signal.dto';

@Injectable()
export class SignalsService {
  constructor(
    @InjectRepository(Signal)
    private readonly signalsRepository: Repository<Signal>,
  ) {}

  async findAll(query: { limit?: number; offset?: number }) {
    const limit = query.limit ?? 50;
    const offset = query.offset ?? 0;

    const [data, total] = await this.signalsRepository.findAndCount({
      order: { createdAt: 'DESC' },
      take: limit,
      skip: offset,
    });

    return { data, total, limit, offset };
  }

  async findById(id: string) {
    const signal = await this.signalsRepository.findOne({ where: { id } });
    if (!signal) {
      throw new NotFoundException(`Signal with id ${id} not found`);
    }
    return signal;
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
