import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import {
  ConflictException,
  NotFoundException,
  ForbiddenException,
  BadGatewayException,
  InternalServerErrorException,
  HttpException,
  HttpStatus,
} from '@nestjs/common';
import { HttpService } from '@nestjs/axios';
import { of, throwError } from 'rxjs';
import { AccountsService } from './accounts.service';
import { TradingAccount } from '../trades/entities/trading-account.entity';
import { PortfolioSnapshot } from '../portfolios/entities/portfolio-snapshot.entity';
import { AccountStrategy } from './entities/account-strategy.entity';
import { InstrumentsService } from '../instruments/instruments.service';
import { EXECUTION_ENGINE_CIRCUIT_BREAKER } from '../../common/circuit-breaker/circuit-breaker.module';
import { REDIS_CLIENT } from '../../common/modules/redis.module';
import { BackfillService } from '../market-data/backfill.service';

describe('AccountsService', () => {
  let service: AccountsService;
  let mockRepo: any;
  let mockHttpService: Record<string, jest.Mock>;
  let mockInstrumentsService: Record<string, jest.Mock>;
  let mockCircuitBreaker: any;

  const userId = 'user-uuid-1';
  const accountId = 'account-uuid-1';

  beforeEach(async () => {
    mockRepo = {
      findOne: jest.fn(),
      find: jest.fn(),
      create: jest.fn((data: any) => ({ ...data })),
      save: jest.fn((entity: any) => Promise.resolve({ ...entity, id: accountId, createdAt: new Date() })),
      remove: jest.fn().mockResolvedValue(undefined),
      manager: { query: jest.fn().mockResolvedValue(undefined) },
    };

    mockHttpService = {
      post: jest.fn(),
      get: jest.fn(),
    };

    mockInstrumentsService = {
      autoAssociateDefaults: jest.fn().mockResolvedValue(undefined),
      getAccountInstruments: jest.fn().mockResolvedValue([]),
    };

    mockCircuitBreaker = {
      execute: jest.fn(async (fn: () => Promise<any>) => fn()),
      getStatus: jest.fn(),
      currentState: 'closed',
    };

    const mockRedis = {
      get: jest.fn().mockResolvedValue(null),
      set: jest.fn().mockResolvedValue('OK'),
      publish: jest.fn().mockResolvedValue(1),
    };

    const mockBackfillService = {
      triggerBackfill: jest.fn().mockResolvedValue(undefined),
      stopStream: jest.fn().mockResolvedValue(undefined),
    };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        AccountsService,
        { provide: getRepositoryToken(TradingAccount), useValue: mockRepo },
        { provide: getRepositoryToken(PortfolioSnapshot), useValue: { findOne: jest.fn().mockResolvedValue(null) } },
        { provide: getRepositoryToken(AccountStrategy), useValue: { find: jest.fn().mockResolvedValue([]), delete: jest.fn().mockResolvedValue(undefined), create: jest.fn(), save: jest.fn() } },
        { provide: HttpService, useValue: mockHttpService },
        { provide: InstrumentsService, useValue: mockInstrumentsService },
        { provide: EXECUTION_ENGINE_CIRCUIT_BREAKER, useValue: mockCircuitBreaker },
        { provide: REDIS_CLIENT, useValue: mockRedis },
        { provide: BackfillService, useValue: mockBackfillService },
      ],
    }).compile();

    service = module.get<AccountsService>(AccountsService);
  });

  describe('create', () => {
    const dto = {
      login: '12345',
      password: 'secret',
      serverName: 'ICMarkets-Demo',
      platform: 'mt5',
      label: 'My Account',
    };

    it('should provision and persist a new account', async () => {
      mockRepo.findOne.mockResolvedValue(null);
      mockHttpService.post.mockReturnValue(
        of({ data: { metaapi_account_id: 'meta-123', state: 'DEPLOYED' } }),
      );

      const result = await service.create(userId, dto);

      expect(result.metaapiAccountId).toBe('meta-123');
      expect(result.userId).toBe(userId);
      expect(result.label).toBe('My Account');
      expect(result.mt5Login).toBe('12345');
      expect(result.mt5Server).toBe('ICMarkets-Demo');
      expect(result.isActive).toBe(true);
    });

    it('should throw ConflictException for duplicate login+server', async () => {
      mockRepo.findOne.mockResolvedValue({ id: 'existing' });

      await expect(service.create(userId, dto)).rejects.toThrow(
        ConflictException,
      );
      expect(mockHttpService.post).not.toHaveBeenCalled();
    });

    it('should default label to login when not provided', async () => {
      const noLabelDto = { ...dto, label: undefined };
      mockRepo.findOne.mockResolvedValue(null);
      mockHttpService.post.mockReturnValue(
        of({ data: { metaapi_account_id: 'meta-456', state: 'DEPLOYED' } }),
      );

      const result = await service.create(userId, noLabelDto);

      expect(result.label).toBe('12345');
    });

    it('should throw BadGatewayException when execution engine is unreachable', async () => {
      mockRepo.findOne.mockResolvedValue(null);
      mockHttpService.post.mockReturnValue(
        throwError(() => new Error('ECONNREFUSED')),
      );

      await expect(service.create(userId, dto)).rejects.toThrow(
        BadGatewayException,
      );
    });
  });

  describe('findAllByUser', () => {
    it('should return only active accounts for the user', async () => {
      const accounts = [
        { id: 'a1', userId, isActive: true },
        { id: 'a2', userId, isActive: true },
      ];
      mockRepo.find.mockResolvedValue(accounts);

      const result = await service.findAllByUser(userId);

      expect(result).toHaveLength(2);
      expect(mockRepo.find).toHaveBeenCalledWith({
        where: { userId, isActive: true },
      });
    });
  });

  describe('getDetails', () => {
    it('should return account details with defaults when execution engine is unavailable', async () => {
      mockRepo.findOne.mockResolvedValue({
        id: accountId,
        userId,
        metaapiAccountId: 'meta-123',
      });
      mockInstrumentsService.getAccountInstruments.mockResolvedValue([]);

      const result = await service.getDetails(userId, accountId);

      expect(result.instruments).toEqual([]);
      expect(result.balance).toBe(0);
      expect(result.equity).toBe(0);
    });

    it('should throw NotFoundException when account does not exist', async () => {
      mockRepo.findOne.mockResolvedValue(null);

      await expect(service.getDetails(userId, accountId)).rejects.toThrow(
        NotFoundException,
      );
    });

    it('should throw ForbiddenException when user does not own account', async () => {
      mockRepo.findOne.mockResolvedValue({
        id: accountId,
        userId: 'other-user',
      });

      await expect(service.getDetails(userId, accountId)).rejects.toThrow(
        ForbiddenException,
      );
    });
  });

  describe('updateLabel', () => {
    it('should update and return the account with new label', async () => {
      mockRepo.findOne.mockResolvedValue({
        id: accountId,
        userId,
        label: 'Old Label',
      });

      const result = await service.updateLabel(userId, accountId, 'New Label');

      expect(result.label).toBe('New Label');
      expect(mockRepo.save).toHaveBeenCalled();
    });

    it('should throw NotFoundException when account does not exist', async () => {
      mockRepo.findOne.mockResolvedValue(null);

      await expect(
        service.updateLabel(userId, accountId, 'Label'),
      ).rejects.toThrow(NotFoundException);
    });

    it('should throw ForbiddenException when user does not own account', async () => {
      mockRepo.findOne.mockResolvedValue({
        id: accountId,
        userId: 'other-user',
      });

      await expect(
        service.updateLabel(userId, accountId, 'Label'),
      ).rejects.toThrow(ForbiddenException);
    });
  });

  describe('remove', () => {
    it('should remove the account from MetaAPI and database', async () => {
      const account = {
        id: accountId,
        userId,
        metaapiAccountId: 'meta-123',
        isActive: true,
      };
      mockRepo.findOne.mockResolvedValue(account);
      mockHttpService.post.mockReturnValue(
        of({ data: { success: true } }),
      );

      await service.remove(userId, accountId);

      expect(mockRepo.remove).toHaveBeenCalledWith(account);
    });

    it('should throw when execution engine remove fails with non-404', async () => {
      const account = {
        id: accountId,
        userId,
        metaapiAccountId: 'meta-123',
        isActive: true,
      };
      mockRepo.findOne.mockResolvedValue(account);
      mockHttpService.post.mockReturnValue(
        throwError(() => ({ response: { status: 500 } })),
      );

      await expect(service.remove(userId, accountId)).rejects.toThrow(
        InternalServerErrorException,
      );
    });

    it('should throw NotFoundException when account does not exist', async () => {
      mockRepo.findOne.mockResolvedValue(null);

      await expect(service.remove(userId, accountId)).rejects.toThrow(
        NotFoundException,
      );
    });

    it('should throw ForbiddenException when user does not own account', async () => {
      mockRepo.findOne.mockResolvedValue({
        id: accountId,
        userId: 'other-user',
      });

      await expect(service.remove(userId, accountId)).rejects.toThrow(
        ForbiddenException,
      );
    });
  });
});

describe('AccountsService - getBrokerSymbols', () => {
  let service: AccountsService;
  let mockRepo: any;
  let mockHttpService: Record<string, jest.Mock>;
  let mockInstrumentsService: Record<string, jest.Mock>;
  let mockCircuitBreaker: any;

  const userId = 'user-uuid-1';
  const accountId = 'account-uuid-1';

  beforeEach(async () => {
    mockRepo = {
      findOne: jest.fn(),
      find: jest.fn(),
      create: jest.fn((data: any) => ({ ...data })),
      save: jest.fn((entity: any) => Promise.resolve({ ...entity, id: accountId, createdAt: new Date() })),
      remove: jest.fn().mockResolvedValue(undefined),
      manager: { query: jest.fn().mockResolvedValue(undefined) },
    };

    mockHttpService = {
      post: jest.fn(),
      get: jest.fn(),
    };

    mockInstrumentsService = {
      autoAssociateDefaults: jest.fn().mockResolvedValue(undefined),
      getAccountInstruments: jest.fn().mockResolvedValue([]),
    };

    mockCircuitBreaker = {
      execute: jest.fn(async (fn: () => Promise<any>) => fn()),
      getStatus: jest.fn(),
      currentState: 'closed',
    };

    const mockRedis = {
      get: jest.fn().mockResolvedValue(null),
      set: jest.fn().mockResolvedValue('OK'),
      publish: jest.fn().mockResolvedValue(1),
    };

    const mockBackfillService = {
      triggerBackfill: jest.fn().mockResolvedValue(undefined),
      stopStream: jest.fn().mockResolvedValue(undefined),
    };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        AccountsService,
        { provide: getRepositoryToken(TradingAccount), useValue: mockRepo },
        { provide: getRepositoryToken(PortfolioSnapshot), useValue: { findOne: jest.fn().mockResolvedValue(null) } },
        { provide: getRepositoryToken(AccountStrategy), useValue: { find: jest.fn().mockResolvedValue([]), delete: jest.fn().mockResolvedValue(undefined), create: jest.fn(), save: jest.fn() } },
        { provide: HttpService, useValue: mockHttpService },
        { provide: InstrumentsService, useValue: mockInstrumentsService },
        { provide: EXECUTION_ENGINE_CIRCUIT_BREAKER, useValue: mockCircuitBreaker },
        { provide: REDIS_CLIENT, useValue: mockRedis },
        { provide: BackfillService, useValue: mockBackfillService },
      ],
    }).compile();

    service = module.get<AccountsService>(AccountsService);
  });

  it('should return symbols from execution engine', async () => {
    mockRepo.findOne.mockResolvedValue({
      id: accountId,
      userId,
      metaapiAccountId: 'meta-123',
    });
    mockHttpService.get.mockReturnValue(
      of({ data: { symbols: ['US30.raw', 'XAUUSD.r', 'DJ30'] } }),
    );

    const result = await service.getBrokerSymbols(userId, accountId);

    expect(result).toEqual(['US30.raw', 'XAUUSD.r', 'DJ30']);
    expect(mockHttpService.get).toHaveBeenCalledWith(
      expect.stringContaining('/accounts/meta-123/symbols'),
      expect.objectContaining({ timeout: 120000 }),
    );
  });

  it('should throw NotFoundException for non-existent account', async () => {
    mockRepo.findOne.mockResolvedValue(null);

    await expect(service.getBrokerSymbols(userId, accountId)).rejects.toThrow(
      NotFoundException,
    );
    expect(mockHttpService.get).not.toHaveBeenCalled();
  });

  it('should throw ForbiddenException for non-owned account', async () => {
    mockRepo.findOne.mockResolvedValue({
      id: accountId,
      userId: 'other-user',
      metaapiAccountId: 'meta-123',
    });

    await expect(service.getBrokerSymbols(userId, accountId)).rejects.toThrow(
      ForbiddenException,
    );
    expect(mockHttpService.get).not.toHaveBeenCalled();
  });

  it('should throw BadGatewayException when execution engine is down', async () => {
    mockRepo.findOne.mockResolvedValue({
      id: accountId,
      userId,
      metaapiAccountId: 'meta-123',
    });
    mockHttpService.get.mockReturnValue(
      throwError(() => new Error('ECONNREFUSED')),
    );

    await expect(service.getBrokerSymbols(userId, accountId)).rejects.toThrow(
      BadGatewayException,
    );
  });
});

