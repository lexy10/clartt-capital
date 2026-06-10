import {
  Injectable,
  ConflictException,
  NotFoundException,
  BadRequestException,
} from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { In, Repository } from 'typeorm';
import { Instrument } from './entities/instrument.entity';
import { AccountInstrument } from './entities/account-instrument.entity';
import { CreateInstrumentDto } from './dto/create-instrument.dto';
import { UpdateInstrumentDto } from './dto/update-instrument.dto';
import { AccountInstrumentItemDto } from './dto/set-account-instruments.dto';

@Injectable()
export class InstrumentsService {
  constructor(
    @InjectRepository(Instrument)
    private readonly instrumentRepo: Repository<Instrument>,
    @InjectRepository(AccountInstrument)
    private readonly accountInstrumentRepo: Repository<AccountInstrument>,
  ) {}

  async findAll(): Promise<Instrument[]> {
    return this.instrumentRepo.find();
  }

  async findAllActive(): Promise<Instrument[]> {
    return this.instrumentRepo.find({ where: { isActive: true } });
  }

  async findById(id: string): Promise<Instrument> {
    const instrument = await this.instrumentRepo.findOne({ where: { id } });
    if (!instrument) {
      throw new NotFoundException('Instrument not found');
    }
    return instrument;
  }

  async create(dto: CreateInstrumentDto): Promise<Instrument> {
    const existing = await this.instrumentRepo.findOne({
      where: { symbol: dto.symbol },
    });
    if (existing) {
      throw new ConflictException(
        `Instrument with symbol '${dto.symbol}' already exists`,
      );
    }
    const instrument = this.instrumentRepo.create(dto);
    return this.instrumentRepo.save(instrument);
  }

  async update(id: string, dto: UpdateInstrumentDto): Promise<Instrument> {
    const instrument = await this.findById(id);
    Object.assign(instrument, dto);
    return this.instrumentRepo.save(instrument);
  }

  async softDelete(id: string): Promise<Instrument> {
    const instrument = await this.findById(id);
    instrument.isActive = false;
    return this.instrumentRepo.save(instrument);
  }

  async getAccountInstruments(
    accountId: string,
  ): Promise<AccountInstrument[]> {
    return this.accountInstrumentRepo.find({
      where: { accountId },
      relations: ['instrument'],
    });
  }

  async setAccountInstruments(
    accountId: string,
    items: AccountInstrumentItemDto[],
  ): Promise<AccountInstrument[]> {
    // Validate all instrument IDs exist
    const instrumentIds = items.map((item) => item.instrumentId);
    if (instrumentIds.length > 0) {
      const instruments = await this.instrumentRepo.find({
        where: { id: In(instrumentIds) },
      });
      const foundIds = new Set(instruments.map((i) => i.id));
      for (const id of instrumentIds) {
        if (!foundIds.has(id)) {
          throw new BadRequestException(`Instrument ID '${id}' not found`);
        }
      }

      // Build a map of instrumentId -> canonical symbol for defaulting
      const symbolMap = new Map(instruments.map((i) => [i.id, i.symbol]));

      // Delete existing associations and insert new ones
      await this.accountInstrumentRepo.delete({ accountId });

      const entities = items.map((item) =>
        this.accountInstrumentRepo.create({
          accountId,
          instrumentId: item.instrumentId,
          brokerSymbol:
            item.brokerSymbol || symbolMap.get(item.instrumentId) || '',
        }),
      );

      return this.accountInstrumentRepo.save(entities);
    }

    // If no items, just clear existing associations
    await this.accountInstrumentRepo.delete({ accountId });
    return [];
  }

  async clearAccountInstruments(accountId: string): Promise<void> {
    await this.accountInstrumentRepo.delete({ accountId });
  }

  async autoAssociateDefaults(accountId: string): Promise<void> {
    const activeDefaults = await this.instrumentRepo.find({
      where: { isActive: true },
    });

    const entities = activeDefaults.map((instrument) =>
      this.accountInstrumentRepo.create({
        accountId,
        instrumentId: instrument.id,
        brokerSymbol: instrument.symbol,
      }),
    );

    await this.accountInstrumentRepo.save(entities);
  }

  async validateInstrumentSymbol(symbol: string): Promise<boolean> {
    const instrument = await this.instrumentRepo.findOne({
      where: { symbol, isActive: true },
    });
    return !!instrument;
  }

  async findBySymbol(symbol: string): Promise<Instrument | null> {
    return this.instrumentRepo.findOne({ where: { symbol } });
  }

  async getBrokerSymbol(
    accountId: string,
    instrumentSymbol: string,
  ): Promise<string> {
    const mapping = await this.accountInstrumentRepo
      .createQueryBuilder('ai')
      .innerJoinAndSelect('ai.instrument', 'instrument')
      .where('ai.account_id = :accountId', { accountId })
      .andWhere('instrument.symbol = :symbol', { symbol: instrumentSymbol })
      .getOne();

    if (mapping) {
      return mapping.brokerSymbol;
    }

    // Fallback to canonical symbol
    return instrumentSymbol;
  }
}
