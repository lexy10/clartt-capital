import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import { NotFoundException, ForbiddenException, ConflictException } from '@nestjs/common';
import { HttpService } from '@nestjs/axios';
import { AutopilotService } from './autopilot.service';
import { AutopilotState } from './entities/autopilot-state.entity';
import { TradingAccount } from '../trades/entities/trading-account.entity';
import { Position } from '../trades/entities/position.entity';
import { REDIS_CLIENT } from '../../common/modules/redis.module';
import { TradingGateway } from '../gateway/trading.gateway';
import { EventPublisherService } from '../events/event-publisher.service';

describe('AutopilotService', () => {
  let service: AutopilotService;
  let mockAutopilotRepo: Record<string, jest.Mock>;
  let mockTradingAccountRepo: Record<string, jest.Mock>;
  let mockPositionRepo: Record<string, jest.Mock>;
  let mockRedis: Record<string, jest.Mock>;
  let mockTradingGateway: Record<string, jest.Mock>;
  let mockEventPublisher: Record<string, jest.Mock>;
  let mockHttpService: Record<string, jest.Mock>;

  beforeEach(async () => {
    mockAutopilotRepo = {
      findOne: jest.fn(),
      find: jest.fn(),
      create: jest.fn((data: any) => ({ ...data })),
      save: jest.fn((entity: any) => Promise.resolve({ ...entity, updatedAt: new Date() })),
    };

    mockTradingAccountRepo = {
      findOne: jest.fn(),
    };

    mockPositionRepo = {
      createQueryBuilder: jest.fn().mockReturnValue({
        select: jest.fn().mockReturnThis(),
        addSelect: jest.fn().mockReturnThis(),
        groupBy: jest.fn().mockReturnThis(),
        getRawMany: jest.fn().mockResolvedValue([]),
      }),
    };

    mockRedis = {
      get: jest.fn().mockResolvedValue(null),
      set: jest.fn().mockResolvedValue('OK'),
      publish: jest.fn().mockResolvedValue(1),
      xlen: jest.fn().mockResolvedValue(0),
    };

    mockTradingGateway = {
      emitAutopilotStateChange: jest.fn(),
      emitMasterAutopilotChange: jest.fn(),
    };

    mockEventPublisher = {
      publish: jest.fn().mockResolvedValue(undefined),
    };

    mockHttpService = {
      post: jest.fn(),
    };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        AutopilotService,
        { provide: getRepositoryToken(AutopilotState), useValue: mockAutopilotRepo },
        { provide: getRepositoryToken(TradingAccount), useValue: mockTradingAccountRepo },
        { provide: getRepositoryToken(Position), useValue: mockPositionRepo },
        { provide: REDIS_CLIENT, useValue: mockRedis },
        { provide: TradingGateway, useValue: mockTradingGateway },
        { provide: HttpService, useValue: mockHttpService },
        { provide: EventPublisherService, useValue: mockEventPublisher },
      ],
    }).compile();

    service = module.get<AutopilotService>(AutopilotService);
  });

  describe('setAutopilotState', () => {
    const accountId = 'account-uuid-1';
    const userId = 'user-uuid-1';

    it('should enable autopilot and persist to DB and Redis', async () => {
      mockTradingAccountRepo.findOne.mockResolvedValue({ id: accountId, userId });
      mockAutopilotRepo.findOne.mockResolvedValue(null);
      mockRedis.get.mockResolvedValue(null); // kill switch not active

      const result = await service.setAutopilotState(accountId, true, userId);

      expect(result.accountId).toBe(accountId);
      expect(result.enabled).toBe(true);
      expect(result.updatedAt).toBeInstanceOf(Date);
      expect(mockRedis.set).toHaveBeenCalledWith(`autopilot:${accountId}`, 'enabled');
      expect(mockRedis.publish).toHaveBeenCalledWith(
        'autopilot:channel',
        JSON.stringify({ accountId, enabled: true }),
      );
      expect(mockTradingGateway.emitAutopilotStateChange).toHaveBeenCalledWith(
        userId,
        {
          accountId,
          enabled: true,
          updatedAt: result.updatedAt.toISOString(),
        },
      );
    });

    it('should disable autopilot and persist to DB and Redis', async () => {
      mockTradingAccountRepo.findOne.mockResolvedValue({ id: accountId, userId });
      mockAutopilotRepo.findOne.mockResolvedValue({ accountId, enabled: true });

      const result = await service.setAutopilotState(accountId, false, userId);

      expect(result.enabled).toBe(false);
      expect(mockRedis.set).toHaveBeenCalledWith(`autopilot:${accountId}`, 'disabled');
      expect(mockRedis.publish).toHaveBeenCalledWith(
        'autopilot:channel',
        JSON.stringify({ accountId, enabled: false }),
      );
      expect(mockTradingGateway.emitAutopilotStateChange).toHaveBeenCalledWith(
        userId,
        {
          accountId,
          enabled: false,
          updatedAt: result.updatedAt.toISOString(),
        },
      );
    });

    it('should throw NotFoundException when account does not exist', async () => {
      mockTradingAccountRepo.findOne.mockResolvedValue(null);

      await expect(
        service.setAutopilotState(accountId, true, userId),
      ).rejects.toThrow(NotFoundException);
      expect(mockTradingGateway.emitAutopilotStateChange).not.toHaveBeenCalled();
    });

    it('should throw ForbiddenException when user does not own the account', async () => {
      mockTradingAccountRepo.findOne.mockResolvedValue({
        id: accountId,
        userId: 'other-user-uuid',
      });

      await expect(
        service.setAutopilotState(accountId, true, userId),
      ).rejects.toThrow(ForbiddenException);
      expect(mockTradingGateway.emitAutopilotStateChange).not.toHaveBeenCalled();
    });

    it('should throw ConflictException when enabling with kill switch active', async () => {
      mockTradingAccountRepo.findOne.mockResolvedValue({ id: accountId, userId });
      mockRedis.get.mockResolvedValue('active');

      await expect(
        service.setAutopilotState(accountId, true, userId),
      ).rejects.toThrow(ConflictException);
      expect(mockTradingGateway.emitAutopilotStateChange).not.toHaveBeenCalled();
    });

    it('should allow disabling even when kill switch is active', async () => {
      mockTradingAccountRepo.findOne.mockResolvedValue({ id: accountId, userId });
      mockAutopilotRepo.findOne.mockResolvedValue({ accountId, enabled: true });
      mockRedis.get.mockResolvedValue('active');

      const result = await service.setAutopilotState(accountId, false, userId);

      expect(result.enabled).toBe(false);
    });

    it('should retry Redis SET on failure and succeed', async () => {
      mockTradingAccountRepo.findOne.mockResolvedValue({ id: accountId, userId });
      mockAutopilotRepo.findOne.mockResolvedValue(null);
      mockRedis.get.mockResolvedValue(null);
      mockRedis.set
        .mockRejectedValueOnce(new Error('Connection lost'))
        .mockResolvedValue('OK');

      const result = await service.setAutopilotState(accountId, true, userId);

      expect(result.enabled).toBe(true);
      expect(mockRedis.set).toHaveBeenCalledTimes(2);
    });

    it('should log error after all Redis retries fail but still return result', async () => {
      mockTradingAccountRepo.findOne.mockResolvedValue({ id: accountId, userId });
      mockAutopilotRepo.findOne.mockResolvedValue(null);
      mockRedis.get.mockResolvedValue(null);
      mockRedis.set.mockRejectedValue(new Error('Connection lost'));

      const result = await service.setAutopilotState(accountId, true, userId);

      // PostgreSQL write succeeded, so result is returned even though Redis failed
      expect(result.accountId).toBe(accountId);
      expect(result.enabled).toBe(true);
      expect(mockRedis.set).toHaveBeenCalledTimes(3);
    });
  });

  describe('getAutopilotState', () => {
    const accountId = 'account-uuid-1';

    it('should return state from Redis when available', async () => {
      mockRedis.get.mockResolvedValue('enabled');

      const result = await service.getAutopilotState(accountId);

      expect(result.accountId).toBe(accountId);
      expect(result.enabled).toBe(true);
    });

    it('should fall back to PostgreSQL when Redis returns null', async () => {
      mockRedis.get.mockResolvedValue(null);
      mockAutopilotRepo.findOne.mockResolvedValue({
        accountId,
        enabled: true,
        updatedAt: new Date(),
      });

      const result = await service.getAutopilotState(accountId);

      expect(result.accountId).toBe(accountId);
      expect(result.enabled).toBe(true);
    });

    it('should fall back to PostgreSQL when Redis throws', async () => {
      mockRedis.get.mockRejectedValue(new Error('Connection lost'));
      mockAutopilotRepo.findOne.mockResolvedValue({
        accountId,
        enabled: false,
        updatedAt: new Date(),
      });

      const result = await service.getAutopilotState(accountId);

      expect(result.enabled).toBe(false);
    });

    it('should return disabled state when not found in either store', async () => {
      mockRedis.get.mockResolvedValue(null);
      mockAutopilotRepo.findOne.mockResolvedValue(null);

      const result = await service.getAutopilotState(accountId);

      expect(result.accountId).toBe(accountId);
      expect(result.enabled).toBe(false);
    });
  });

  describe('syncStatesToRedis', () => {
    it('should sync all states from PostgreSQL to Redis', async () => {
      mockAutopilotRepo.find.mockResolvedValue([
        { accountId: 'acc-1', enabled: true },
        { accountId: 'acc-2', enabled: false },
        { accountId: 'acc-3', enabled: true },
      ]);

      await service.syncStatesToRedis();

      expect(mockRedis.set).toHaveBeenCalledWith('autopilot:acc-1', 'enabled');
      expect(mockRedis.set).toHaveBeenCalledWith('autopilot:acc-2', 'disabled');
      expect(mockRedis.set).toHaveBeenCalledWith('autopilot:acc-3', 'enabled');
    });

    it('should handle empty state list', async () => {
      mockAutopilotRepo.find.mockResolvedValue([]);

      await service.syncStatesToRedis();

      // Only the master autopilot key should be set (initialized to disabled)
      expect(mockRedis.set).toHaveBeenCalledTimes(1);
      expect(mockRedis.set).toHaveBeenCalledWith('autopilot:master', 'disabled');
    });

    it('should continue syncing other states when one Redis write fails', async () => {
      mockAutopilotRepo.find.mockResolvedValue([
        { accountId: 'acc-1', enabled: true },
        { accountId: 'acc-2', enabled: false },
      ]);
      mockRedis.set
        .mockRejectedValueOnce(new Error('Connection lost'))
        .mockResolvedValue('OK');

      await service.syncStatesToRedis();

      // 2 account syncs + 1 master autopilot init = 3 calls
      expect(mockRedis.set).toHaveBeenCalledTimes(3);
      expect(mockRedis.set).toHaveBeenCalledWith('autopilot:acc-2', 'disabled');
    });
  });

  describe('event publishing', () => {
    const accountId = 'account-uuid-1';
    const userId = 'user-uuid-1';

    it('should publish AutopilotStateChanged event on per-account state change', async () => {
      mockTradingAccountRepo.findOne.mockResolvedValue({ id: accountId, userId });
      mockAutopilotRepo.findOne.mockResolvedValue(null);
      mockRedis.get.mockResolvedValue(null);

      await service.setAutopilotState(accountId, true, userId);

      // Allow fire-and-forget promise to resolve
      await new Promise((r) => setTimeout(r, 10));

      expect(mockEventPublisher.publish).toHaveBeenCalledWith(
        expect.objectContaining({
          event_type: 'AutopilotStateChanged',
          aggregate_id: `autopilot:${accountId}`,
          payload: expect.objectContaining({
            scope: 'account',
            account_id: accountId,
            new_state: true,
            changed_by: userId,
          }),
          context_snapshot: expect.objectContaining({
            open_positions_per_account: expect.any(Object),
            pending_signals_count: expect.any(Number),
            kill_switch_active: expect.any(Boolean),
          }),
        }),
      );
    });

    it('should publish AutopilotStateChanged event on master state change', async () => {
      mockRedis.get.mockResolvedValue(null);

      await service.setMasterAutopilot(true);

      // Allow fire-and-forget promise to resolve
      await new Promise((r) => setTimeout(r, 10));

      expect(mockEventPublisher.publish).toHaveBeenCalledWith(
        expect.objectContaining({
          event_type: 'AutopilotStateChanged',
          aggregate_id: 'autopilot:master',
          payload: expect.objectContaining({
            scope: 'master',
            account_id: null,
            new_state: true,
            changed_by: 'system',
          }),
        }),
      );
    });

    it('should not block state change when event publishing fails', async () => {
      mockTradingAccountRepo.findOne.mockResolvedValue({ id: accountId, userId });
      mockAutopilotRepo.findOne.mockResolvedValue(null);
      mockRedis.get.mockResolvedValue(null);
      mockEventPublisher.publish.mockRejectedValue(new Error('Redis down'));

      const result = await service.setAutopilotState(accountId, true, userId);

      expect(result.accountId).toBe(accountId);
      expect(result.enabled).toBe(true);
    });
  });
});
