import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Signal } from './entities/signal.entity';
import { SignalsService } from './signals.service';
import { SignalsController } from './signals.controller';

@Module({
  imports: [TypeOrmModule.forFeature([Signal])],
  controllers: [SignalsController],
  providers: [SignalsService],
  exports: [TypeOrmModule, SignalsService],
})
export class SignalsModule {}
