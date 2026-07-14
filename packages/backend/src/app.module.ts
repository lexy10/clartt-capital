import { Module } from '@nestjs/common';
import { ConfigModule, ConfigService } from '@nestjs/config';
import { TypeOrmModule } from '@nestjs/typeorm';
import { APP_INTERCEPTOR, APP_GUARD } from '@nestjs/core';
import { ThrottlerModule, ThrottlerGuard } from '@nestjs/throttler';
import { LoggingInterceptor } from './common/interceptors/logging.interceptor';
import { MetricsInterceptor } from './common/interceptors/metrics.interceptor';
import { EffectiveUserInterceptor } from './common/interceptors/effective-user.interceptor';
import { RedisModule } from './common/modules/redis.module';
import { QueueModule } from './common/modules/queue.module';
import { AuthModule } from './modules/auth/auth.module';
import { UsersModule } from './modules/users/users.module';
import { TradesModule } from './modules/trades/trades.module';
import { StrategiesModule } from './modules/strategies/strategies.module';
import { SignalsModule } from './modules/signals/signals.module';
import { WatchlistsModule } from './modules/watchlists/watchlists.module';
import { AlertsModule } from './modules/alerts/alerts.module';
import { PortfoliosModule } from './modules/portfolios/portfolios.module';
import { AdminModule } from './modules/admin/admin.module';
import { AutopilotModule } from './modules/autopilot/autopilot.module';
import { GatewayModule } from './modules/gateway/gateway.module';
import { MarketDataModule } from './modules/market-data/market-data.module';
import { MetricsModule } from './modules/metrics/metrics.module';
import { AccountsModule } from './modules/accounts/accounts.module';
import { PerformanceModule } from './modules/performance/performance.module';
import { InstrumentsModule } from './modules/instruments/instruments.module';
import { ReconciliationModule } from './modules/reconciliation/reconciliation.module';
import { EventsModule } from './modules/events/events.module';
import { CircuitBreakerModule } from './common/circuit-breaker/circuit-breaker.module';
import { HealthModule } from './modules/health/health.module';
import { AgentsModule } from './modules/agents/agents.module';
import { SeedModule } from './common/seed/seed.module';
import { PortfolioSnapshotWorker } from './common/workers/portfolio-snapshot.worker';
import { AlertEvaluationWorker } from './common/workers/alert-evaluation.worker';
import { Alert } from './modules/alerts/entities/alert.entity';

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      envFilePath: ['.env'],
    }),
    // Global rate limit, keyed on the real client IP (see the trust-proxy
    // setup in main.ts — without it this would throttle ALL users as one).
    // The dashboard is chatty: LiveDesk polls several endpoints every 30s and
    // account_sync WebSocket events trigger refetches, so the per-user budget
    // is generous. Auth endpoints carry a much stricter per-route override
    // (see auth.controller.ts) to block brute-force.
    ThrottlerModule.forRoot([
      {
        ttl: 60_000,
        limit: 600,
      },
    ]),
    TypeOrmModule.forRootAsync({
      imports: [ConfigModule],
      inject: [ConfigService],
      useFactory: (config: ConfigService) => ({
        type: 'postgres' as const,
        url: config.get<string>('DATABASE_URL'),
        autoLoadEntities: true,
        synchronize: config.get<string>('NODE_ENV') !== 'production',
        // Production schema management: synchronize is off there, so
        // migrations are the only way schema changes reach the VPS. They
        // run automatically at boot. The compiled migrations land in
        // dist/migrations by nest build.
        // NOTE: a DB restored from a dev dump has the full schema but no
        // migrations history — baseline it first (deploy/vps-restore-db.sh
        // does this) or every migration will re-run and fail.
        migrations: [__dirname + '/migrations/*.js'],
        migrationsRun: config.get<string>('NODE_ENV') === 'production',
        // Only log errors + slow queries (>500ms). Logging every query in
        // dev floods stdout when the candle ingestion runs and stalls the
        // event loop — making all API endpoints (including overview) feel
        // sluggish. Errors and slow queries are still surfaced.
        logging: ['error', 'warn', 'schema', 'migration'],
        maxQueryExecutionTime: 500,
        // The candle subscriber writes one INSERT per minute-candle across
        // every tracked instrument — the default pg pool (10) saturates
        // quickly and starves API requests of connections. Bump to 30 so
        // overview / accounts endpoints always have a free slot.
        extra: {
          max: 30,
        },
      }),
    }),
    RedisModule,
    QueueModule,
    TypeOrmModule.forFeature([Alert]),
    AuthModule,
    UsersModule,
    TradesModule,
    StrategiesModule,
    SignalsModule,
    WatchlistsModule,
    AlertsModule,
    PortfoliosModule,
    AdminModule,
    AutopilotModule,
    GatewayModule,
    MarketDataModule,
    MetricsModule,
    AccountsModule,
    PerformanceModule,
    InstrumentsModule,
    ReconciliationModule,
    EventsModule,
    CircuitBreakerModule,
    HealthModule,
    AgentsModule,
    SeedModule,
  ],
  providers: [
    {
      provide: APP_GUARD,
      useClass: ThrottlerGuard,
    },
    {
      provide: APP_INTERCEPTOR,
      useClass: LoggingInterceptor,
    },
    {
      provide: APP_INTERCEPTOR,
      useClass: MetricsInterceptor,
    },
    // Must run AFTER the JWT guard populates req.user. Nest runs global
    // interceptors after guards, so registration order doesn't matter for
    // that ordering — they always fire post-guard.
    {
      provide: APP_INTERCEPTOR,
      useClass: EffectiveUserInterceptor,
    },
    PortfolioSnapshotWorker,
    AlertEvaluationWorker,
  ],
})
export class AppModule {}
