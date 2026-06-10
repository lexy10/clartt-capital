import { Module } from '@nestjs/common';
import { CircuitBreaker } from './circuit-breaker';
import { CircuitBreakerMetrics } from './circuit-breaker-metrics.service';
import { ConsumerLagMonitor } from './consumer-lag-monitor.service';
import { GatewayModule } from '../../modules/gateway/gateway.module';
import { TradingGateway } from '../../modules/gateway/trading.gateway';

export const EXECUTION_ENGINE_CIRCUIT_BREAKER = 'EXECUTION_ENGINE_CIRCUIT_BREAKER';

@Module({
  imports: [GatewayModule],
  providers: [
    CircuitBreakerMetrics,
    ConsumerLagMonitor,
    {
      provide: EXECUTION_ENGINE_CIRCUIT_BREAKER,
      useFactory: (gateway: TradingGateway, metrics: CircuitBreakerMetrics) =>
        new CircuitBreaker({
          name: 'backend-to-execution-engine',
          onStateChange: (name, previousState, newState) => {
            metrics.onStateTransition(name, newState);
            gateway.emitCircuitBreakerStateChange({
              name,
              previousState,
              newState,
              timestamp: new Date().toISOString(),
            });
          },
        }),
      inject: [TradingGateway, CircuitBreakerMetrics],
    },
  ],
  exports: [EXECUTION_ENGINE_CIRCUIT_BREAKER, CircuitBreakerMetrics, ConsumerLagMonitor],
})
export class CircuitBreakerModule {}
