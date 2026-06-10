import { Repository } from 'typeorm';
import { Instrument } from './entities/instrument.entity';

const DEFAULT_INSTRUMENTS: Partial<Instrument>[] = [
  {
    symbol: 'US30',
    displayName: 'US30',
    type: 'index',
    isActive: true,
    contractSize: 1,
    pipSize: 1.0,
    pipValue: 1.0,
    minLot: 0.01,
    lotStep: 0.01,
    leverage: 100,
  },
  {
    symbol: 'XAUUSD',
    displayName: 'XAUUSD',
    type: 'commodity',
    isActive: true,
    contractSize: 100,
    pipSize: 0.01,
    pipValue: 1.0,
    minLot: 0.01,
    lotStep: 0.01,
    leverage: 100,
  },
  {
    symbol: 'R_75',
    displayName: 'Volatility 75 Index',
    type: 'synthetic',
    isActive: true,
    contractSize: 1,
    pipSize: 0.001,
    pipValue: 1.0,
    minLot: 0.01,
    lotStep: 0.01,
    leverage: 500,
  },
  {
    symbol: 'R_25',
    displayName: 'Volatility 25 Index',
    type: 'synthetic',
    isActive: true,
    contractSize: 1,
    pipSize: 0.001,
    pipValue: 1.0,
    minLot: 0.01,
    lotStep: 0.01,
    leverage: 500,
  },
];

export async function seedInstruments(
  repo: Repository<Instrument>,
): Promise<void> {
  for (const instrument of DEFAULT_INSTRUMENTS) {
    const exists = await repo.findOne({ where: { symbol: instrument.symbol } });
    if (!exists) {
      await repo.save(repo.create(instrument));
    }
  }
}
