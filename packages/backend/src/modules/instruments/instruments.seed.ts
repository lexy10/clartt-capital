import { Repository } from 'typeorm';
import { Logger } from '@nestjs/common';
import { Instrument } from './entities/instrument.entity';

/**
 * Default instrument catalogue. Exported from the working dev DB so a fresh
 * deployment (no full-dump restore) comes up with the exact instruments the
 * platform trades — including their `derivSymbol` mapping and contract specs.
 *
 * WHY derivSymbol matters: the candle backfill (backfill.service.ts) filters
 * to `instruments.filter(i => i.derivSymbol)` and SKIPS auto-start entirely
 * if none have one. The old seed omitted derivSymbol, so a fresh DB would
 * come up with instruments but never backfill a single candle. seedInstruments
 * now also self-heals existing rows that are missing derivSymbol.
 *
 * Synthetics route to Deriv; US30/XAUUSD are MetaAPI and ship inactive
 * (no live MT5 credentials by default).
 *
 * ACTIVE vs inactive: only instruments the shipped strategies actually trade
 * (R_25, R_75) are active. The candle backfill only pulls history + streams
 * for ACTIVE instruments, so keeping the rest inactive avoids storing candles
 * you don't use — candles are ~99% of DB size, and each extra active
 * instrument adds ~200 MB/year. All instruments are still seeded (so they're
 * one toggle away in the dashboard); they just don't backfill until activated.
 */
const DEFAULT_INSTRUMENTS: Partial<Instrument>[] = [
  // ── Volatility indices (Deriv) — only R_25 & R_75 active (shipped strategies) ──
  {
    symbol: 'R_10', displayName: 'Volatility 10 Index', type: 'synthetic',
    derivSymbol: 'R_10', category: 'synthetic', preferredProvider: 'deriv',
    isActive: false, contractSize: 1, pipSize: 0.001, pipValue: 1.0,
    minLot: 0.5, lotStep: 0.001, leverage: 500,
  },
  {
    symbol: 'R_25', displayName: 'Volatility 25 Index', type: 'synthetic',
    derivSymbol: 'R_25', category: 'synthetic', preferredProvider: 'deriv',
    isActive: true, contractSize: 1, pipSize: 0.01, pipValue: 1.0,
    minLot: 0.001, lotStep: 0.001, leverage: 500,
  },
  {
    symbol: 'R_50', displayName: 'Volatility 50 Index', type: 'synthetic',
    derivSymbol: 'R_50', category: 'synthetic', preferredProvider: 'deriv',
    isActive: false, contractSize: 1, pipSize: 0.0001, pipValue: 1.0,
    minLot: 4.0, lotStep: 0.001, leverage: 500,
  },
  {
    symbol: 'R_75', displayName: 'Volatility 75 Index', type: 'synthetic',
    derivSymbol: 'R_75', category: 'synthetic', preferredProvider: 'deriv',
    isActive: true, contractSize: 1, pipSize: 0.01, pipValue: 1.0,
    minLot: 0.001, lotStep: 0.001, leverage: 500,
  },
  {
    symbol: 'R_100', displayName: 'Volatility 100 Index', type: 'synthetic',
    derivSymbol: 'R_100', category: 'synthetic', preferredProvider: 'deriv',
    isActive: false, contractSize: 1, pipSize: 0.01, pipValue: 1.0,
    minLot: 0.5, lotStep: 0.001, leverage: 500,
  },
  // ── Boom / Crash (Deriv) — seeded but inactive (no shipped strategy) ──
  {
    symbol: 'BOOM_500', displayName: 'Boom 500 Index', type: 'synthetic',
    derivSymbol: 'BOOM500', category: 'synthetic', preferredProvider: 'deriv',
    isActive: false, contractSize: 1, pipSize: 0.001, pipValue: 1.0,
    minLot: 0.2, lotStep: 0.001, leverage: 100,
  },
  {
    symbol: 'BOOM_1000', displayName: 'Boom 1000 Index', type: 'synthetic',
    derivSymbol: 'BOOM1000', category: 'synthetic', preferredProvider: 'deriv',
    isActive: false, contractSize: 1, pipSize: 0.001, pipValue: 1.0,
    minLot: 0.2, lotStep: 0.001, leverage: 100,
  },
  {
    symbol: 'CRASH_500', displayName: 'Crash 500 Index', type: 'synthetic',
    derivSymbol: 'CRASH500', category: 'synthetic', preferredProvider: 'deriv',
    isActive: false, contractSize: 1, pipSize: 0.001, pipValue: 1.0,
    minLot: 0.2, lotStep: 0.001, leverage: 100,
  },
  {
    symbol: 'CRASH_1000', displayName: 'Crash 1000 Index', type: 'synthetic',
    derivSymbol: 'CRASH1000', category: 'synthetic', preferredProvider: 'deriv',
    isActive: false, contractSize: 1, pipSize: 0.001, pipValue: 1.0,
    minLot: 0.2, lotStep: 0.001, leverage: 100,
  },
  // ── MetaAPI instruments (inactive until MT5 creds are configured) ──
  {
    symbol: 'US30', displayName: 'US30', type: 'index',
    derivSymbol: 'OTC_DJI', category: 'index', preferredProvider: 'metaapi',
    isActive: false, contractSize: 1, pipSize: 1.0, pipValue: 1.0,
    minLot: 0.01, lotStep: 0.01, leverage: 100,
  },
  {
    symbol: 'XAUUSD', displayName: 'XAUUSD', type: 'commodity',
    derivSymbol: 'frxXAUUSD', category: 'commodity', preferredProvider: 'metaapi',
    isActive: false, contractSize: 100, pipSize: 0.01, pipValue: 1.0,
    minLot: 0.01, lotStep: 0.01, leverage: 100,
  },
];

const logger = new Logger('InstrumentsSeed');

export async function seedInstruments(
  repo: Repository<Instrument>,
): Promise<void> {
  let created = 0;
  let healed = 0;
  for (const instrument of DEFAULT_INSTRUMENTS) {
    const exists = await repo.findOne({ where: { symbol: instrument.symbol } });
    if (!exists) {
      await repo.save(repo.create(instrument));
      created++;
      continue;
    }
    // Self-heal: an older DB (or one where the AddDerivSymbol migration ran
    // before rows existed) may have the instrument but a NULL derivSymbol,
    // which silently disables candle backfill. Backfill it in place.
    if (!exists.derivSymbol && instrument.derivSymbol) {
      exists.derivSymbol = instrument.derivSymbol;
      await repo.save(exists);
      healed++;
    }
  }
  if (created || healed) {
    logger.log(`Instrument seed: ${created} created, ${healed} derivSymbol-healed`);
  }
}
