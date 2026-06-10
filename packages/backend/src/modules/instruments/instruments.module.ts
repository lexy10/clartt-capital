import { Module, OnModuleInit } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Instrument } from './entities/instrument.entity';
import { AccountInstrument } from './entities/account-instrument.entity';
import { InstrumentsService } from './instruments.service';
import { InstrumentsController } from './instruments.controller';
import { seedInstruments } from './instruments.seed';

@Module({
  imports: [TypeOrmModule.forFeature([Instrument, AccountInstrument])],
  controllers: [InstrumentsController],
  providers: [InstrumentsService],
  exports: [InstrumentsService],
})
export class InstrumentsModule implements OnModuleInit {
  constructor(
    @InjectRepository(Instrument)
    private readonly instrumentRepo: Repository<Instrument>,
  ) {}

  async onModuleInit(): Promise<void> {
    await seedInstruments(this.instrumentRepo);
  }
}
