import { Inject, Injectable, Logger } from '@nestjs/common';
import { validate } from 'class-validator';
import { instanceToPlain } from 'class-transformer';
import Redis from 'ioredis';
import { REDIS_CLIENT } from '@/common/modules/redis.module';
import { TradingEventDto } from './dto/trading-event.dto';

@Injectable()
export class EventPublisherService {
  private readonly logger = new Logger(EventPublisherService.name);
  private readonly STREAM_KEY = 'events:stream';
  private readonly MAX_LEN = '10000';

  constructor(@Inject(REDIS_CLIENT) private readonly redis: Redis) {}

  async publish(event: TradingEventDto): Promise<void> {
    try {
      event.source_service = 'backend';

      const errors = await validate(event);
      if (errors.length > 0) {
        this.logger.error(
          `Event validation failed: ${errors.map((e) => Object.values(e.constraints ?? {})).flat().join(', ')}`,
        );
        return;
      }

      const payload = JSON.stringify(instanceToPlain(event));
      await this.redis.xadd(
        this.STREAM_KEY,
        'MAXLEN',
        '~',
        this.MAX_LEN,
        '*',
        'data',
        payload,
      );
    } catch (err) {
      this.logger.error(`Failed to publish event: ${(err as Error).message}`);
    }
  }
}
