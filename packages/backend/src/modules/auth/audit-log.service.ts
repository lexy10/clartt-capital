import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { AuditLog } from './entities/audit-log.entity';

@Injectable()
export class AuditLogService {
  private readonly logger = new Logger(AuditLogService.name);

  constructor(
    @InjectRepository(AuditLog)
    private readonly auditLogRepo: Repository<AuditLog>,
  ) {}

  async log(
    eventType: string,
    userId: string | null,
    ipAddress: string | null,
    details?: Record<string, unknown>,
  ): Promise<void> {
    try {
      const entry = this.auditLogRepo.create({
        eventType,
        userId,
        ipAddress,
        details: details ?? null,
      });
      await this.auditLogRepo.save(entry);
    } catch (error) {
      // Audit logging should never break the main flow
      this.logger.error(`Failed to write audit log: ${error}`);
    }
  }
}
