import {
  Injectable,
  CanActivate,
  ExecutionContext,
  HttpException,
  HttpStatus,
  Inject,
} from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import Redis from 'ioredis';
import { REDIS_CLIENT } from '../modules/redis.module';
import { RATE_LIMIT_KEY, RateLimitOptions } from '../decorators/rate-limit.decorator';

const DEFAULT_LIMIT = 100;
const DEFAULT_WINDOW_SECONDS = 60;

@Injectable()
export class RateLimitGuard implements CanActivate {
  constructor(
    private readonly reflector: Reflector,
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
  ) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const options = this.reflector.getAllAndOverride<RateLimitOptions>(RATE_LIMIT_KEY, [
      context.getHandler(),
      context.getClass(),
    ]);

    const limit = options?.limit ?? DEFAULT_LIMIT;
    const windowSeconds = options?.windowSeconds ?? DEFAULT_WINDOW_SECONDS;

    const request = context.switchToHttp().getRequest();
    const ip = request.ip || request.connection?.remoteAddress || 'unknown';
    const route = request.method + ':' + request.route?.path || request.url;
    const key = `rate_limit:${ip}:${route}`;

    const current = await this.redis.incr(key);

    if (current === 1) {
      await this.redis.expire(key, windowSeconds);
    }

    if (current > limit) {
      throw new HttpException(
        {
          error: 'Too Many Requests',
          code: 'RATE_LIMIT_EXCEEDED',
          retryAfter: windowSeconds,
        },
        HttpStatus.TOO_MANY_REQUESTS,
      );
    }

    return true;
  }
}
