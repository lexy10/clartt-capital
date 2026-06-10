export enum DiscrepancyType {
  MISSING_POSITION = 'missing_position',
  PHANTOM_POSITION = 'phantom_position',
  BALANCE_DRIFT = 'balance_drift',
  EQUITY_DRIFT = 'equity_drift',
  POSITION_SIZE_MISMATCH = 'position_size_mismatch',
  DIRECTION_MISMATCH = 'direction_mismatch',
}

export enum DiscrepancySeverity {
  WARNING = 'warning',
  CRITICAL = 'critical',
}
