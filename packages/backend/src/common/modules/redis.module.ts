import { Module, Global, OnModuleDestroy, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import Redis from 'ioredis';

export const REDIS_CLIENT = 'REDIS_CLIENT';

@Global()
@Module({
  providers: [
    {
      provide: REDIS_CLIENT,
      useFactory: (config: ConfigService) => {
        const logger = new Logger('RedisModule');
        const redisUrl = config.get<string>('REDIS_URL') || 'redis://localhost:6379';
        const client = new Redis(redisUrl, {
          maxRetriesPerRequest: 3,
          retryStrategy: (times: number) => Math.min(times * 200, 2000),
        });

        client.on('connect', () => {
          logger.log('Redis connected — full functionality restored');
        });

        client.on('close', () => {
          logger.warn(
            'Redis connection closed — real-time features (WebSocket updates, stream consumption) are degraded',
          );
        });

        client.on('reconnecting', (delay: number) => {
          logger.warn(`Redis reconnecting in ${delay}ms`);
        });

        client.on('error', (err: Error) => {
          logger.error(`Redis error: ${err.message}`);
        });

        return client;
      },
      inject: [ConfigService],
    },
  ],
  exports: [REDIS_CLIENT],
})
export class RedisModule implements OnModuleDestroy {
  constructor(private readonly config: ConfigService) {}

  async onModuleDestroy() {
    // Redis client cleanup is handled by NestJS DI container
  }
}
