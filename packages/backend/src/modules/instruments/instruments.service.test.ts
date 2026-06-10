import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import { InstrumentsService } from './instruments.service';
import { Instrument } from './entities/instrument.entity';
import { AccountInstrument } from './entities/account-instrument.entity';

describe('InstrumentsService', () => {
  let service: InstrumentsService;
  let instrumentRepo: Record<string, jest.Mock>;
  let accountInstrumentRepo: Record<string, jest.Mock>;

  beforeEach(async () => {
    instrumentRepo = {
      find: jest.fn(),
      findOne: jest.fn(),
      create: jest.fn((data) => ({ id: 'inst-uuid', ...data })),
      save: jest.fn((entity) => Promise.resolve({ ...entity })),
    };

    accountInstrumentRepo = {
      find: jest.fn(),
      findOne: jest.fn(),
      create: jest.fn((data) => ({ id: 'ai-uuid', ...data })),
      save: jest.fn((entity) => Promise.resolve(Array.isArray(entity) ? entity : { ...entity })),
      delete: jest.fn().mockResolvedValue({ affected: 1 }),
      createQueryBuilder: jest.fn(),
    };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        InstrumentsService,
        { provide: getRepositoryToken(Instrument), useValue: instrumentRepo },
        { provide: getRepositoryToken(AccountInstrument), useValue: accountInstrumentRepo },
      ],
    }).compile();

    service = module.get<InstrumentsService>(InstrumentsService);
  });

  describe('getBrokerSymbol', () => {
    function mockQueryBuilder(result: any) {
      const qb = {
        innerJoinAndSelect: jest.fn().mockReturnThis(),
        where: jest.fn().mockReturnThis(),
        andWhere: jest.fn().mockReturnThis(),
        getOne: jest.fn().mockResolvedValue(result),
      };
      accountInstrumentRepo.createQueryBuilder.mockReturnValue(qb);
      return qb;
    }

    it('should return the broker symbol from AccountInstrument mapping when it exists', async () => {
      const mapping = { brokerSymbol: 'US30.raw' };
      const qb = mockQueryBuilder(mapping);

      const result = await service.getBrokerSymbol('account-123', 'US30');

      expect(result).toBe('US30.raw');
      expect(accountInstrumentRepo.createQueryBuilder).toHaveBeenCalledWith('ai');
      expect(qb.innerJoinAndSelect).toHaveBeenCalledWith('ai.instrument', 'instrument');
      expect(qb.where).toHaveBeenCalledWith('ai.account_id = :accountId', { accountId: 'account-123' });
      expect(qb.andWhere).toHaveBeenCalledWith('instrument.symbol = :symbol', { symbol: 'US30' });
      expect(qb.getOne).toHaveBeenCalled();
    });

    it('should return the canonical instrument symbol when no mapping exists', async () => {
      mockQueryBuilder(null);

      const result = await service.getBrokerSymbol('account-456', 'XAUUSD');

      expect(result).toBe('XAUUSD');
    });
  });
});
