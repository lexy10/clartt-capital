import { DiscrepancyType, DiscrepancySeverity } from './enums';

export interface BrokerPosition {
  symbol: string;
  direction: 'BUY' | 'SELL';
  volume: number;
  openPrice: number;
  profit: number;
  id: string;
}

export interface BrokerAccountInfo {
  balance: number;
  equity: number;
  margin: number;
  freeMargin: number;
}

export interface LocalPositionState {
  id: string;
  instrument: string;
  direction: string;
  positionSize: string;
  entryPrice: string;
}

export interface SymbolMapping {
  localSymbol: string;
  brokerSymbol: string;
}

export interface Discrepancy {
  type: DiscrepancyType;
  severity: DiscrepancySeverity;
  details: Record<string, unknown>;
  localPositionId?: string;
  brokerPositionId?: string;
  instrument?: string;
  localValue?: number;
  brokerValue?: number;
  drift?: number;
}

export interface CorrectionResult {
  type: DiscrepancyType;
  success: boolean;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  error?: string;
}

export interface EffectiveConfig {
  reconciliationIntervalSeconds: number;
  balanceDriftThreshold: number;
  equityDriftThreshold: number;
  positionSizeDriftThreshold: number;
  autoCorrectPhantomPositions: boolean;
  autoCorrectMissingPositions: boolean;
  autoCorrectBalanceDrift: boolean;
  escalationCycleCount: number;
}

export interface ReconciliationDiscrepancyPayload {
  accountId: string;
  timestamp: string;
  discrepancies: {
    type: DiscrepancyType;
    severity: DiscrepancySeverity;
    details: Record<string, unknown>;
  }[];
}
