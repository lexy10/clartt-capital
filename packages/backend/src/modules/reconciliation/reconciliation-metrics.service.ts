import { Injectable } from '@nestjs/common';
import { Counter, Histogram, Gauge } from 'prom-client';

@Injectable()
export class ReconciliationMetrics {
  readonly cyclesTotal = new Counter({
    name: 'reconciliation_cycles_total',
    help: 'Total number of reconciliation cycles executed',
    labelNames: ['account_id', 'status'] as const,
  });

  readonly discrepanciesTotal = new Counter({
    name: 'reconciliation_discrepancies_total',
    help: 'Total number of discrepancies detected',
    labelNames: ['account_id', 'discrepancy_type'] as const,
  });

  readonly cycleDuration = new Histogram({
    name: 'reconciliation_cycle_duration_seconds',
    help: 'Duration of reconciliation cycles in seconds',
    labelNames: ['account_id'] as const,
    buckets: [0.1, 0.5, 1, 2, 5, 10, 30],
  });

  readonly autoCorrectionsTotal = new Counter({
    name: 'reconciliation_auto_corrections_total',
    help: 'Total number of auto-corrections applied',
    labelNames: ['account_id', 'correction_type'] as const,
  });

  readonly persistentDiscrepancies = new Gauge({
    name: 'reconciliation_persistent_discrepancies',
    help: 'Current count of persistent discrepancies per account',
    labelNames: ['account_id', 'discrepancy_type'] as const,
  });

  incrementCycles(accountId: string, status: string): void {
    this.cyclesTotal.inc({ account_id: accountId, status });
  }

  incrementDiscrepancies(accountId: string, discrepancyType: string): void {
    this.discrepanciesTotal.inc({ account_id: accountId, discrepancy_type: discrepancyType });
  }

  observeCycleDuration(accountId: string, durationSeconds: number): void {
    this.cycleDuration.observe({ account_id: accountId }, durationSeconds);
  }

  incrementAutoCorrections(accountId: string, correctionType: string): void {
    this.autoCorrectionsTotal.inc({ account_id: accountId, correction_type: correctionType });
  }

  setPersistentDiscrepancies(accountId: string, discrepancyType: string, count: number): void {
    this.persistentDiscrepancies.set({ account_id: accountId, discrepancy_type: discrepancyType }, count);
  }
}
