import { Controller, Get } from '@nestjs/common';
import { HealthService, HealthResponse, CircuitBreakerAggregateResponse } from './health.service';

@Controller('health')
export class HealthController {
  constructor(private readonly healthService: HealthService) {}

  @Get()
  getHealth(): HealthResponse {
    // Mark PostgreSQL as healthy on each request (proves DB connection works)
    this.healthService.markPostgresSuccess();
    return this.healthService.getHealth();
  }

  @Get('circuit-breakers')
  async getCircuitBreakers(): Promise<CircuitBreakerAggregateResponse> {
    return this.healthService.getCircuitBreakers();
  }

  @Get('services/strategy-engine')
  async getStrategyEngineHealth() {
    return this.healthService.fetchServiceHealthProxy('strategy-engine');
  }

  @Get('services/execution-engine')
  async getExecutionEngineHealth() {
    return this.healthService.fetchServiceHealthProxy('execution-engine');
  }
}
