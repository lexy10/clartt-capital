import {
  Injectable,
  NestInterceptor,
  ExecutionContext,
  CallHandler,
  Logger,
} from '@nestjs/common';
import { Observable, tap } from 'rxjs';
import { Request, Response } from 'express';

@Injectable()
export class LoggingInterceptor implements NestInterceptor {
  private readonly logger = new Logger('HTTP');

  intercept(context: ExecutionContext, next: CallHandler): Observable<unknown> {
    const ctx = context.switchToHttp();
    const request = ctx.getRequest<Request>();
    const { method, url, ip } = request;
    const startTime = Date.now();

    return next.handle().pipe(
      tap({
        next: () => {
          const response = ctx.getResponse<Response>();
          const duration = Date.now() - startTime;
          this.logger.log(
            JSON.stringify({
              method,
              url,
              statusCode: response.statusCode,
              duration_ms: duration,
              ip,
              timestamp: new Date().toISOString(),
            }),
          );
        },
        error: (error: Error & { status?: number }) => {
          const duration = Date.now() - startTime;
          this.logger.error(
            JSON.stringify({
              method,
              url,
              statusCode: error.status || 500,
              duration_ms: duration,
              ip,
              error: error.message,
              timestamp: new Date().toISOString(),
            }),
          );
        },
      }),
    );
  }
}
