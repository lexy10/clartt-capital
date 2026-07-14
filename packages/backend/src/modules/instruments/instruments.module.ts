import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Instrument } from './entities/instrument.entity';
import { AccountInstrument } from './entities/account-instrument.entity';
import { InstrumentsService } from './instruments.service';
import { InstrumentsController } from './instruments.controller';

// NOTE: instrument seeding moved to the central SeedModule
// (common/seed/seed.module.ts) so it runs in a deterministic order with the
// admin-user and strategy seeds on onApplicationBootstrap.
@Module({
  imports: [TypeOrmModule.forFeature([Instrument, AccountInstrument])],
  controllers: [InstrumentsController],
  providers: [InstrumentsService],
  exports: [InstrumentsService],
})
export class InstrumentsModule {}
