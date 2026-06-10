import { Injectable, NotFoundException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Alert } from './entities/alert.entity';
import { CreateAlertDto } from './dto/create-alert.dto';

@Injectable()
export class AlertsService {
  constructor(
    @InjectRepository(Alert)
    private readonly alertsRepository: Repository<Alert>,
  ) {}

  async findAllByUser(userId: string): Promise<Alert[]> {
    return this.alertsRepository.find({ where: { userId } });
  }

  async create(userId: string, dto: CreateAlertDto): Promise<Alert> {
    const alert = this.alertsRepository.create({
      userId,
      instrument: dto.instrument,
      conditionType: dto.conditionType,
      conditionValue: dto.conditionValue,
    });
    return this.alertsRepository.save(alert);
  }

  async remove(userId: string, id: string): Promise<void> {
    const alert = await this.alertsRepository.findOne({
      where: { id, userId },
    });
    if (!alert) {
      throw new NotFoundException('Alert not found');
    }
    await this.alertsRepository.remove(alert);
  }
}
