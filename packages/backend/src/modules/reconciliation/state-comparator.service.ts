import { Injectable } from '@nestjs/common';
import {
  BrokerPosition,
  BrokerAccountInfo,
  LocalPositionState,
  SymbolMapping,
  Discrepancy,
} from './types';
import { DiscrepancyType, DiscrepancySeverity } from './types';

interface PositionThresholds {
  positionSizeDrift: number;
}

interface BalanceThresholds {
  balanceDrift: number;
  equityDrift: number;
}

@Injectable()
export class StateComparator {
  comparePositions(
    localPositions: LocalPositionState[],
    brokerPositions: BrokerPosition[],
    symbolMappings: SymbolMapping[],
    thresholds: PositionThresholds,
  ): Discrepancy[] {
    const discrepancies: Discrepancy[] = [];

    // Build broker→local symbol lookup
    const brokerToLocal = new Map<string, string>();
    for (const mapping of symbolMappings) {
      brokerToLocal.set(
        mapping.brokerSymbol.toUpperCase(),
        mapping.localSymbol.toUpperCase(),
      );
    }

    // Normalize broker positions to local symbol space
    // Composite key: "SYMBOL|DIRECTION"
    const brokerByKey = new Map<string, BrokerPosition>();
    for (const bp of brokerPositions) {
      const normalizedSymbol =
        brokerToLocal.get(bp.symbol.toUpperCase()) ?? bp.symbol.toUpperCase();
      const key = `${normalizedSymbol}|${bp.direction.toUpperCase()}`;
      brokerByKey.set(key, bp);
    }

    const localByKey = new Map<string, LocalPositionState>();
    for (const lp of localPositions) {
      const key = `${lp.instrument.toUpperCase()}|${lp.direction.toUpperCase()}`;
      localByKey.set(key, lp);
    }

    // Check each broker position against local
    for (const [key, bp] of brokerByKey) {
      const lp = localByKey.get(key);
      if (!lp) {
        // Missing position: exists at broker but not locally
        const normalizedSymbol = key.split('|')[0];
        discrepancies.push({
          type: DiscrepancyType.MISSING_POSITION,
          severity: DiscrepancySeverity.CRITICAL,
          brokerPositionId: bp.id,
          instrument: normalizedSymbol,
          details: {
            brokerSymbol: bp.symbol,
            direction: bp.direction,
            volume: bp.volume,
            openPrice: bp.openPrice,
          },
        });
        continue;
      }

      // Matched by (symbol, direction) — check size
      const localSize = parseFloat(lp.positionSize);
      const brokerSize = bp.volume;
      if (Math.abs(localSize - brokerSize) > thresholds.positionSizeDrift) {
        discrepancies.push({
          type: DiscrepancyType.POSITION_SIZE_MISMATCH,
          severity: DiscrepancySeverity.CRITICAL,
          localPositionId: lp.id,
          brokerPositionId: bp.id,
          instrument: lp.instrument,
          localValue: localSize,
          brokerValue: brokerSize,
          drift: Math.abs(localSize - brokerSize),
          details: {
            localSize,
            brokerSize,
            threshold: thresholds.positionSizeDrift,
          },
        });
      }
    }

    // Check each local position against broker — detect phantoms
    for (const [key, lp] of localByKey) {
      const bp = brokerByKey.get(key);
      if (!bp) {
        discrepancies.push({
          type: DiscrepancyType.PHANTOM_POSITION,
          severity: DiscrepancySeverity.CRITICAL,
          localPositionId: lp.id,
          instrument: lp.instrument,
          details: {
            localSymbol: lp.instrument,
            direction: lp.direction,
            positionSize: lp.positionSize,
            entryPrice: lp.entryPrice,
          },
        });
      }
    }

    // Direction mismatch: positions matched by symbol only but with different directions
    // Build symbol-only maps to find cross-direction matches
    const brokerBySymbol = new Map<string, BrokerPosition[]>();
    for (const bp of brokerPositions) {
      const normalizedSymbol =
        brokerToLocal.get(bp.symbol.toUpperCase()) ?? bp.symbol.toUpperCase();
      const existing = brokerBySymbol.get(normalizedSymbol) ?? [];
      existing.push(bp);
      brokerBySymbol.set(normalizedSymbol, existing);
    }

    const localBySymbol = new Map<string, LocalPositionState[]>();
    for (const lp of localPositions) {
      const sym = lp.instrument.toUpperCase();
      const existing = localBySymbol.get(sym) ?? [];
      existing.push(lp);
      localBySymbol.set(sym, existing);
    }

    for (const [symbol, localList] of localBySymbol) {
      const brokerList = brokerBySymbol.get(symbol);
      if (!brokerList) continue;

      for (const lp of localList) {
        const directionMatch = brokerList.find(
          (bp) => bp.direction.toUpperCase() === lp.direction.toUpperCase(),
        );
        if (directionMatch) continue;

        // There are broker positions for this symbol but none with matching direction
        const oppositeMatch = brokerList.find(
          (bp) => bp.direction.toUpperCase() !== lp.direction.toUpperCase(),
        );
        if (oppositeMatch) {
          // Only flag direction mismatch if neither was already flagged as missing/phantom
          const localKey = `${lp.instrument.toUpperCase()}|${lp.direction.toUpperCase()}`;
          const brokerKey = `${symbol}|${oppositeMatch.direction.toUpperCase()}`;
          const localIsPhantom = !brokerByKey.has(localKey);
          const brokerIsMissing = !localByKey.has(brokerKey);

          if (localIsPhantom && brokerIsMissing) {
            discrepancies.push({
              type: DiscrepancyType.DIRECTION_MISMATCH,
              severity: DiscrepancySeverity.CRITICAL,
              localPositionId: lp.id,
              brokerPositionId: oppositeMatch.id,
              instrument: lp.instrument,
              details: {
                localDirection: lp.direction,
                brokerDirection: oppositeMatch.direction,
              },
            });
          }
        }
      }
    }

    return discrepancies;
  }

  compareBalances(
    localSnapshot: { balance: string; equity: string } | null,
    brokerInfo: BrokerAccountInfo,
    thresholds: BalanceThresholds,
  ): Discrepancy[] {
    if (!localSnapshot) {
      return [];
    }

    const discrepancies: Discrepancy[] = [];

    const localBalance = parseFloat(localSnapshot.balance);
    const brokerBalance = brokerInfo.balance;
    const balanceDiff = Math.abs(localBalance - brokerBalance);

    if (balanceDiff > thresholds.balanceDrift) {
      discrepancies.push({
        type: DiscrepancyType.BALANCE_DRIFT,
        severity: DiscrepancySeverity.WARNING,
        localValue: localBalance,
        brokerValue: brokerBalance,
        drift: balanceDiff,
        details: {
          localBalance,
          brokerBalance,
          threshold: thresholds.balanceDrift,
        },
      });
    }

    const localEquity = parseFloat(localSnapshot.equity);
    const brokerEquity = brokerInfo.equity;
    const equityDiff = Math.abs(localEquity - brokerEquity);

    if (equityDiff > thresholds.equityDrift) {
      discrepancies.push({
        type: DiscrepancyType.EQUITY_DRIFT,
        severity: DiscrepancySeverity.WARNING,
        localValue: localEquity,
        brokerValue: brokerEquity,
        drift: equityDiff,
        details: {
          localEquity,
          brokerEquity,
          threshold: thresholds.equityDrift,
        },
      });
    }

    return discrepancies;
  }
}
