import {
  Injectable,
  NestInterceptor,
  ExecutionContext,
  CallHandler,
  Logger,
} from '@nestjs/common';
import { Observable, tap } from 'rxjs';
import { Request } from 'express';
import * as client from 'prom-client';

// Collect default Node.js metrics (CPU, memory, event loop, etc.)
client.collectDefaultMetrics();

export const httpRequestDuration = new client.Histogram({
  name: 'http_request_duration_seconds',
  help: 'Duration of HTTP requests in seconds',
  labelNames: ['method', 'route', 'status_code'],
  buckets: [0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5],
});

export const httpRequestsTotal = new client.Counter({
  name: 'http_requests_total',
  help: 'Total number of HTTP requests',
  labelNames: ['method', 'route', 'status_code'],
});

export const tradeExecutionLatency = new client.Histogram({
  name: 'trade_execution_latency_ms',
  help: 'Trade execution latency in milliseconds',
  labelNames: ['status', 'account_id'],
  buckets: [10, 25, 50, 100, 200, 500, 1000, 2500],
});

export const latencyThresholdBreaches = new client.Counter({
  name: 'latency_threshold_breaches_total',
  help: 'Number of times trade execution latency exceeded the configured threshold',
  labelNames: ['account_id'],
});

const LATENCY_THRESHOLD_MS = parseInt(
  process.env.EXECUTION_LATENCY_THRESHOLD_MS || '500',
  10,
);

/**
 * Record a trade execution latency observation and check against threshold.
 */
export function recordTradeLatency(
  latencyMs: number,
  status: string,
  accountId: string,
): void {
  tradeExecutionLatency.observe({ status, account_id: accountId }, latencyMs);
  if (latencyMs > LATENCY_THRESHOLD_MS) {
    latencyThresholdBreaches.inc({ account_id: accountId });
    const logger = new Logger('MetricsInterceptor');
    logger.warn(
      `Trade execution latency ${latencyMs}ms exceeded threshold ${LATENCY_THRESHOLD_MS}ms for account ${accountId}`,
    );
  }
}

@Injectable()
export class MetricsInterceptor implements NestInterceptor {
  intercept(context: ExecutionContext, next: CallHandler): Observable<unknown> {
    const ctx = context.switchToHttp();
    const request = ctx.getRequest<Request>();
    const { method } = request;
    const route = request.route?.path || request.url;
    const end = httpRequestDuration.startTimer({ method, route });

    return next.handle().pipe(
      tap({
        next: () => {
          const statusCode = ctx.getResponse().statusCode;
          end({ status_code: statusCode });
          httpRequestsTotal.inc({ method, route, status_code: statusCode });
        },
        error: (error: Error & { status?: number }) => {
          const statusCode = error.status || 500;
          end({ status_code: statusCode });
          httpRequestsTotal.inc({ method, route, status_code: statusCode });
        },
      }),
    );
  }
}
