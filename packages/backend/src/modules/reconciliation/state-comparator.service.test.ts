import { StateComparator } from './state-comparator.service';
import {
  BrokerPosition,
  BrokerAccountInfo,
  LocalPositionState,
  SymbolMapping,
} from './types';
import { DiscrepancyType, DiscrepancySeverity } from './types';

describe('StateComparator', () => {
  let comparator: StateComparator;

  beforeEach(() => {
    comparator = new StateComparator();
  });

  const defaultThresholds = { positionSizeDrift: 0.01 };
  const defaultBalanceThresholds = { balanceDrift: 10, equityDrift: 50 };

  describe('comparePositions', () => {
    it('should return empty array when both sides are empty', () => {
      const result = comparator.comparePositions([], [], [], defaultThresholds);
      expect(result).toEqual([]);
    });

    it('should return empty array when positions match exactly', () => {
      const local: LocalPositionState[] = [
        { id: 'l1', instrument: 'US30', direction: 'BUY', positionSize: '1.0', entryPrice: '35000' },
      ];
      const broker: BrokerPosition[] = [
        { id: 'b1', symbol: 'US30', direction: 'BUY', volume: 1.0, openPrice: 35000, profit: 100 },
      ];
      const result = comparator.comparePositions(local, broker, [], defaultThresholds);
      expect(result).toEqual([]);
    });

    it('should detect missing_position when broker has position not in local', () => {
      const broker: BrokerPosition[] = [
        { id: 'b1', symbol: 'US30', direction: 'BUY', volume: 1.0, openPrice: 35000, profit: 100 },
      ];
      const result = comparator.comparePositions([], broker, [], defaultThresholds);

      expect(result).toHaveLength(1);
      expect(result[0].type).toBe(DiscrepancyType.MISSING_POSITION);
      expect(result[0].severity).toBe(DiscrepancySeverity.CRITICAL);
      expect(result[0].brokerPositionId).toBe('b1');
    });

    it('should detect phantom_position when local has position not at broker', () => {
      const local: LocalPositionState[] = [
        { id: 'l1', instrument: 'US30', direction: 'BUY', positionSize: '1.0', entryPrice: '35000' },
      ];
      const result = comparator.comparePositions(local, [], [], defaultThresholds);

      expect(result).toHaveLength(1);
      expect(result[0].type).toBe(DiscrepancyType.PHANTOM_POSITION);
      expect(result[0].severity).toBe(DiscrepancySeverity.CRITICAL);
      expect(result[0].localPositionId).toBe('l1');
    });

    it('should detect position_size_mismatch when sizes differ beyond threshold', () => {
      const local: LocalPositionState[] = [
        { id: 'l1', instrument: 'US30', direction: 'BUY', positionSize: '1.0', entryPrice: '35000' },
      ];
      const broker: BrokerPosition[] = [
        { id: 'b1', symbol: 'US30', direction: 'BUY', volume: 1.5, openPrice: 35000, profit: 100 },
      ];
      const result = comparator.comparePositions(local, broker, [], defaultThresholds);

      const sizeMismatch = result.find(d => d.type === DiscrepancyType.POSITION_SIZE_MISMATCH);
      expect(sizeMismatch).toBeDefined();
      expect(sizeMismatch!.severity).toBe(DiscrepancySeverity.CRITICAL);
      expect(sizeMismatch!.localValue).toBe(1.0);
      expect(sizeMismatch!.brokerValue).toBe(1.5);
    });

    it('should NOT flag size mismatch when difference is within threshold', () => {
      const local: LocalPositionState[] = [
        { id: 'l1', instrument: 'US30', direction: 'BUY', positionSize: '1.0', entryPrice: '35000' },
      ];
      const broker: BrokerPosition[] = [
        { id: 'b1', symbol: 'US30', direction: 'BUY', volume: 1.005, openPrice: 35000, profit: 100 },
      ];
      const result = comparator.comparePositions(local, broker, [], defaultThresholds);
      expect(result).toEqual([]);
    });

    it('should use symbol mappings to normalize broker symbols', () => {
      const local: LocalPositionState[] = [
        { id: 'l1', instrument: 'US30', direction: 'BUY', positionSize: '1.0', entryPrice: '35000' },
      ];
      const broker: BrokerPosition[] = [
        { id: 'b1', symbol: 'US30.raw', direction: 'BUY', volume: 1.0, openPrice: 35000, profit: 100 },
      ];
      const mappings: SymbolMapping[] = [
        { localSymbol: 'US30', brokerSymbol: 'US30.raw' },
      ];
      const result = comparator.comparePositions(local, broker, mappings, defaultThresholds);
      expect(result).toEqual([]);
    });

    it('should detect direction_mismatch when symbol matches but direction differs', () => {
      const local: LocalPositionState[] = [
        { id: 'l1', instrument: 'US30', direction: 'BUY', positionSize: '1.0', entryPrice: '35000' },
      ];
      const broker: BrokerPosition[] = [
        { id: 'b1', symbol: 'US30', direction: 'SELL', volume: 1.0, openPrice: 35000, profit: -50 },
      ];
      const result = comparator.comparePositions(local, broker, [], defaultThresholds);

      const dirMismatch = result.find(d => d.type === DiscrepancyType.DIRECTION_MISMATCH);
      expect(dirMismatch).toBeDefined();
      expect(dirMismatch!.severity).toBe(DiscrepancySeverity.CRITICAL);
      expect(dirMismatch!.details).toEqual(
        expect.objectContaining({ localDirection: 'BUY', brokerDirection: 'SELL' }),
      );
    });

    it('should be case-insensitive for symbol and direction matching', () => {
      const local: LocalPositionState[] = [
        { id: 'l1', instrument: 'us30', direction: 'buy', positionSize: '1.0', entryPrice: '35000' },
      ];
      const broker: BrokerPosition[] = [
        { id: 'b1', symbol: 'US30', direction: 'BUY', volume: 1.0, openPrice: 35000, profit: 100 },
      ];
      const result = comparator.comparePositions(local, broker, [], defaultThresholds);
      expect(result).toEqual([]);
    });

    it('should handle multiple positions across different instruments', () => {
      const local: LocalPositionState[] = [
        { id: 'l1', instrument: 'US30', direction: 'BUY', positionSize: '1.0', entryPrice: '35000' },
        { id: 'l2', instrument: 'XAUUSD', direction: 'SELL', positionSize: '0.5', entryPrice: '2000' },
      ];
      const broker: BrokerPosition[] = [
        { id: 'b1', symbol: 'US30', direction: 'BUY', volume: 1.0, openPrice: 35000, profit: 100 },
        { id: 'b2', symbol: 'XAUUSD', direction: 'SELL', volume: 0.5, openPrice: 2000, profit: -20 },
      ];
      const result = comparator.comparePositions(local, broker, [], defaultThresholds);
      expect(result).toEqual([]);
    });
  });

  describe('compareBalances', () => {
    const brokerInfo: BrokerAccountInfo = {
      balance: 10000,
      equity: 10500,
      margin: 200,
      freeMargin: 10300,
    };

    it('should return empty array when snapshot is null (skip balance comparison)', () => {
      const result = comparator.compareBalances(null, brokerInfo, defaultBalanceThresholds);
      expect(result).toEqual([]);
    });

    it('should return empty array when balances match within thresholds', () => {
      const snapshot = { balance: '10005', equity: '10520' };
      const result = comparator.compareBalances(snapshot, brokerInfo, defaultBalanceThresholds);
      expect(result).toEqual([]);
    });

    it('should detect balance_drift when difference exceeds threshold', () => {
      const snapshot = { balance: '9980', equity: '10500' };
      const result = comparator.compareBalances(snapshot, brokerInfo, defaultBalanceThresholds);

      const balanceDrift = result.find(d => d.type === DiscrepancyType.BALANCE_DRIFT);
      expect(balanceDrift).toBeDefined();
      expect(balanceDrift!.severity).toBe(DiscrepancySeverity.WARNING);
      expect(balanceDrift!.localValue).toBe(9980);
      expect(balanceDrift!.brokerValue).toBe(10000);
      expect(balanceDrift!.drift).toBe(20);
    });

    it('should detect equity_drift when difference exceeds threshold', () => {
      const snapshot = { balance: '10000', equity: '10000' };
      const result = comparator.compareBalances(snapshot, brokerInfo, defaultBalanceThresholds);

      const equityDrift = result.find(d => d.type === DiscrepancyType.EQUITY_DRIFT);
      expect(equityDrift).toBeDefined();
      expect(equityDrift!.severity).toBe(DiscrepancySeverity.WARNING);
      expect(equityDrift!.drift).toBe(500);
    });

    it('should detect both balance and equity drift simultaneously', () => {
      const snapshot = { balance: '9000', equity: '9000' };
      const result = comparator.compareBalances(snapshot, brokerInfo, defaultBalanceThresholds);

      expect(result).toHaveLength(2);
      expect(result.map(d => d.type)).toContain(DiscrepancyType.BALANCE_DRIFT);
      expect(result.map(d => d.type)).toContain(DiscrepancyType.EQUITY_DRIFT);
    });

    it('should NOT flag drift when difference equals threshold exactly', () => {
      const snapshot = { balance: '9990', equity: '10450' };
      const result = comparator.compareBalances(snapshot, brokerInfo, defaultBalanceThresholds);
      expect(result).toEqual([]);
    });

    it('should handle custom thresholds', () => {
      const snapshot = { balance: '9995', equity: '10490' };
      const tightThresholds = { balanceDrift: 1, equityDrift: 5 };
      const result = comparator.compareBalances(snapshot, brokerInfo, tightThresholds);

      expect(result).toHaveLength(2);
    });
  });

  describe('severity classification', () => {
    it('should assign critical severity to missing_position', () => {
      const broker: BrokerPosition[] = [
        { id: 'b1', symbol: 'US30', direction: 'BUY', volume: 1.0, openPrice: 35000, profit: 0 },
      ];
      const result = comparator.comparePositions([], broker, [], defaultThresholds);
      expect(result[0].severity).toBe(DiscrepancySeverity.CRITICAL);
    });

    it('should assign critical severity to phantom_position', () => {
      const local: LocalPositionState[] = [
        { id: 'l1', instrument: 'US30', direction: 'BUY', positionSize: '1.0', entryPrice: '35000' },
      ];
      const result = comparator.comparePositions(local, [], [], defaultThresholds);
      expect(result[0].severity).toBe(DiscrepancySeverity.CRITICAL);
    });

    it('should assign warning severity to balance_drift', () => {
      const snapshot = { balance: '9000', equity: '10500' };
      const brokerInfo: BrokerAccountInfo = { balance: 10000, equity: 10500, margin: 0, freeMargin: 0 };
      const result = comparator.compareBalances(snapshot, brokerInfo, defaultBalanceThresholds);
      expect(result[0].severity).toBe(DiscrepancySeverity.WARNING);
    });

    it('should assign warning severity to equity_drift', () => {
      const snapshot = { balance: '10000', equity: '9000' };
      const brokerInfo: BrokerAccountInfo = { balance: 10000, equity: 10500, margin: 0, freeMargin: 0 };
      const result = comparator.compareBalances(snapshot, brokerInfo, defaultBalanceThresholds);
      expect(result[0].severity).toBe(DiscrepancySeverity.WARNING);
    });
  });
});
