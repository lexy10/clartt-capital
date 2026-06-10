import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { HttpModule } from '@nestjs/axios';
import { KillSwitch } from './entities/kill-switch.entity';
import { AdminController } from './admin.controller';
import { AdminService } from './admin.service';
import { AccountsModule } from '../accounts/accounts.module';
import { EventsModule } from '../events/events.module';

@Module({
  imports: [
    TypeOrmModule.forFeature([KillSwitch]),
    HttpModule,
    AccountsModule,
    EventsModule,
  ],
  controllers: [AdminController],
  providers: [AdminService],
  exports: [AdminService],
})
export class AdminModule {}
