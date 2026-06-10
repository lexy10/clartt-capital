import { ReconciliationMetrics } from './reconciliation-metrics.service';
import * as client from 'prom-client';

describe('ReconciliationMetrics', () => {
  let metrics: ReconciliationMetrics;

  beforeEach(() => {
    client.register.clear();
    metrics = new ReconciliationMetrics();
  });

  afterEach(() => {
    client.register.clear();
  });

  it('should register all five metrics in the default registry', async () => {
    const output = await client.register.metrics();
    expect(output).toContain('reconciliation_cycles_total');
    expect(output).toContain('reconciliation_discrepancies_total');
    expect(output).toContain('reconciliation_cycle_duration_seconds');
    expect(output).toContain('reconciliation_auto_corrections_total');
    expect(output).toContain('reconciliation_persistent_discrepancies');
  });

  it('should increment cycles counter with account_id and status labels', async () => {
    metrics.incrementCycles('acc-1', 'clean');
    metrics.incrementCycles('acc-1', 'clean');
    metrics.incrementCycles('acc-1', 'error');

    const output = await client.register.metrics();
    expect(output).toContain('reconciliation_cycles_total{account_id="acc-1",status="clean"} 2');
    expect(output).toContain('reconciliation_cycles_total{account_id="acc-1",status="error"} 1');
  });

  it('should increment discrepancies counter with discrepancy_type label', async () => {
    metrics.incrementDiscrepancies('acc-2', 'missing_position');
    metrics.incrementDiscrepancies('acc-2', 'balance_drift');

    const output = await client.register.metrics();
    expect(output).toContain('reconciliation_discrepancies_total{account_id="acc-2",discrepancy_type="missing_position"} 1');
    expect(output).toContain('reconciliation_discrepancies_total{account_id="acc-2",discrepancy_type="balance_drift"} 1');
  });

  it('should observe cycle duration in histogram', async () => {
    metrics.observeCycleDuration('acc-3', 1.5);

    const output = await client.register.metrics();
    expect(output).toContain('reconciliation_cycle_duration_seconds_bucket');
    expect(output).toContain('reconciliation_cycle_duration_seconds_sum{account_id="acc-3"} 1.5');
    expect(output).toContain('reconciliation_cycle_duration_seconds_count{account_id="acc-3"} 1');
  });

  it('should increment auto-corrections counter with correction_type label', async () => {
    metrics.incrementAutoCorrections('acc-4', 'phantom_position');

    const output = await client.register.metrics();
    expect(output).toContain('reconciliation_auto_corrections_total{account_id="acc-4",correction_type="phantom_position"} 1');
  });

  it('should set persistent discrepancies gauge', async () => {
    metrics.setPersistentDiscrepancies('acc-5', 'equity_drift', 3);

    const output = await client.register.metrics();
    expect(output).toContain('reconciliation_persistent_discrepancies{account_id="acc-5",discrepancy_type="equity_drift"} 3');
  });

  it('should allow gauge to go up and down', async () => {
    metrics.setPersistentDiscrepancies('acc-6', 'balance_drift', 5);
    metrics.setPersistentDiscrepancies('acc-6', 'balance_drift', 2);

    const output = await client.register.metrics();
    expect(output).toContain('reconciliation_persistent_discrepancies{account_id="acc-6",discrepancy_type="balance_drift"} 2');
  });
});
