import { BadRequestException } from '@nestjs/common';
import { ReconciliationConfigService, UpdateConfigDto } from './reconciliation-config.service';
import { ReconciliationConfig } from './entities/reconciliation-config.entity';
import { EffectiveConfig } from './types';

describe('ReconciliationConfigService', () => {
  let service: ReconciliationConfigService;
  let mockConfigRepo: any;
  let mockRedis: any;

  const DEFAULT_EFFECTIVE: EffectiveConfig = {
    reconciliationIntervalSeconds: 60,
    balanceDriftThreshold: 10,
    equityDriftThreshold: 50,
    positionSizeDriftThreshold: 0.01,
    autoCorrectPhantomPositions: false,
    autoCorrectMissingPositions: false,
    autoCorrectBalanceDrift: false,
    escalationCycleCount: 3,
  };

  function makeEntity(overrides: Partial<ReconciliationConfig> = {}): ReconciliationConfig {
    const entity = new ReconciliationConfig();
    entity.id = 'cfg-1';
    entity.accountId = null;
    entity.reconciliationIntervalSeconds = 60;
    entity.balanceDriftThreshold = '10.00';
    entity.equityDriftThreshold = '50.00';
    entity.positionSizeDriftThreshold = '0.0100';
    entity.autoCorrectPhantomPositions = false;
    entity.autoCorrectMissingPositions = false;
    entity.autoCorrectBalanceDrift = false;
    entity.escalationCycleCount = 3;
    entity.updatedAt = new Date();
    Object.assign(entity, overrides);
    return entity;
  }

  beforeEach(() => {
    mockConfigRepo = {
      findOne: jest.fn(),
      create: jest.fn((data: any) => {
        const entity = new ReconciliationConfig();
        Object.assign(entity, data);
        return entity;
      }),
      save: jest.fn((entity: any) => Promise.resolve({ ...entity, id: entity.id || 'new-id' })),
    };

    mockRedis = {
      get: jest.fn().mockResolvedValue(null),
      set: jest.fn().mockResolvedValue('OK'),
      del: jest.fn().mockResolvedValue(1),
      scan: jest.fn().mockResolvedValue(['0', []]),
    };

    service = new ReconciliationConfigService(mockConfigRepo, mockRedis);
  });

  describe('validateConfig', () => {
    it('should accept valid config', () => {
      expect(() =>
        service.validateConfig({
          reconciliationIntervalSeconds: 30,
          balanceDriftThreshold: 5,
          equityDriftThreshold: 10,
          positionSizeDriftThreshold: 0.001,
          escalationCycleCount: 1,
        }),
      ).not.toThrow();
    });

    it('should reject interval < 30', () => {
      expect(() =>
        service.validateConfig({ reconciliationIntervalSeconds: 29 }),
      ).toThrow(BadRequestException);
    });

    it('should reject balanceDriftThreshold <= 0', () => {
      expect(() =>
        service.validateConfig({ balanceDriftThreshold: 0 }),
      ).toThrow(BadRequestException);
      expect(() =>
        service.validateConfig({ balanceDriftThreshold: -1 }),
      ).toThrow(BadRequestException);
    });

    it('should reject equityDriftThreshold <= 0', () => {
      expect(() =>
        service.validateConfig({ equityDriftThreshold: 0 }),
      ).toThrow(BadRequestException);
    });

    it('should reject positionSizeDriftThreshold <= 0', () => {
      expect(() =>
        service.validateConfig({ positionSizeDriftThreshold: 0 }),
      ).toThrow(BadRequestException);
    });

    it('should reject escalationCycleCount <= 0', () => {
      expect(() =>
        service.validateConfig({ escalationCycleCount: 0 }),
      ).toThrow(BadRequestException);
    });

    it('should collect multiple errors', () => {
      try {
        service.validateConfig({
          reconciliationIntervalSeconds: 10,
          balanceDriftThreshold: -5,
        });
        fail('Expected BadRequestException');
      } catch (err: any) {
        expect(err.message).toContain('reconciliationIntervalSeconds');
        expect(err.message).toContain('balanceDriftThreshold');
      }
    });

    it('should accept empty dto (no fields to validate)', () => {
      expect(() => service.validateConfig({})).not.toThrow();
    });
  });

  describe('getEffectiveConfig', () => {
    const accountId = 'acc-123';

    it('should return cached config from Redis if available', async () => {
      const cached: EffectiveConfig = { ...DEFAULT_EFFECTIVE, reconciliationIntervalSeconds: 90 };
      mockRedis.get.mockResolvedValueOnce(JSON.stringify(cached));

      const result = await service.getEffectiveConfig(accountId);

      expect(result).toEqual(cached);
      expect(mockRedis.get).toHaveBeenCalledWith(`reconciliation:config:${accountId}`);
      expect(mockConfigRepo.findOne).not.toHaveBeenCalled();
    });

    it('should return defaults when no global or account config exists', async () => {
      mockConfigRepo.findOne.mockResolvedValue(null);

      const result = await service.getEffectiveConfig(accountId);

      expect(result).toEqual(DEFAULT_EFFECTIVE);
    });

    it('should use global config when no account config exists', async () => {
      const globalEntity = makeEntity({ reconciliationIntervalSeconds: 120 });
      mockConfigRepo.findOne
        .mockResolvedValueOnce(null)   // account config
        .mockResolvedValueOnce(globalEntity); // global config (called by loadGlobalConfig)

      // Override: loadGlobalConfig is called first internally, then account lookup
      // Actually the flow is: check account cache → loadGlobalConfig → load account from DB
      // loadGlobalConfig checks redis (null) → checks DB for accountId=null
      // Then getEffectiveConfig checks DB for accountId=acc-123
      mockConfigRepo.findOne
        .mockReset()
        .mockImplementation(async (opts: any) => {
          if (opts.where.accountId === null) return globalEntity;
          return null; // no account config
        });

      const result = await service.getEffectiveConfig(accountId);

      expect(result.reconciliationIntervalSeconds).toBe(120);
    });

    it('should override global with per-account config', async () => {
      const globalEntity = makeEntity({ reconciliationIntervalSeconds: 120 });
      const accountEntity = makeEntity({
        accountId: accountId,
        reconciliationIntervalSeconds: 45,
        balanceDriftThreshold: '25.00',
      });

      mockConfigRepo.findOne.mockImplementation(async (opts: any) => {
        if (opts.where.accountId === null) return globalEntity;
        if (opts.where.accountId === accountId) return accountEntity;
        return null;
      });

      const result = await service.getEffectiveConfig(accountId);

      expect(result.reconciliationIntervalSeconds).toBe(45);
      expect(result.balanceDriftThreshold).toBe(25);
    });

    it('should cache effective config in Redis', async () => {
      mockConfigRepo.findOne.mockResolvedValue(null);

      await service.getEffectiveConfig(accountId);

      expect(mockRedis.set).toHaveBeenCalledWith(
        `reconciliation:config:${accountId}`,
        expect.any(String),
      );
    });

    it('should fall back to DB when Redis read fails', async () => {
      mockRedis.get.mockRejectedValue(new Error('Redis down'));
      mockConfigRepo.findOne.mockResolvedValue(null);

      const result = await service.getEffectiveConfig(accountId);

      expect(result).toEqual(DEFAULT_EFFECTIVE);
    });
  });

  describe('updateGlobalConfig', () => {
    it('should create new global config if none exists', async () => {
      mockConfigRepo.findOne.mockResolvedValue(null);

      await service.updateGlobalConfig({ reconciliationIntervalSeconds: 90 });

      expect(mockConfigRepo.create).toHaveBeenCalledWith({ accountId: null });
      expect(mockConfigRepo.save).toHaveBeenCalled();
    });

    it('should update existing global config', async () => {
      const existing = makeEntity();
      mockConfigRepo.findOne.mockResolvedValue(existing);

      await service.updateGlobalConfig({ balanceDriftThreshold: 20 });

      expect(mockConfigRepo.save).toHaveBeenCalledWith(
        expect.objectContaining({ balanceDriftThreshold: '20' }),
      );
    });

    it('should invalidate Redis caches after update', async () => {
      mockConfigRepo.findOne.mockResolvedValue(null);

      await service.updateGlobalConfig({ reconciliationIntervalSeconds: 90 });

      expect(mockRedis.del).toHaveBeenCalledWith('reconciliation:config:global');
    });

    it('should reject invalid config', async () => {
      await expect(
        service.updateGlobalConfig({ reconciliationIntervalSeconds: 10 }),
      ).rejects.toThrow(BadRequestException);
    });
  });

  describe('updateAccountConfig', () => {
    const accountId = 'acc-456';

    it('should create new account config if none exists', async () => {
      mockConfigRepo.findOne.mockResolvedValue(null);

      await service.updateAccountConfig(accountId, {
        reconciliationIntervalSeconds: 45,
      });

      expect(mockConfigRepo.create).toHaveBeenCalledWith({ accountId });
      expect(mockConfigRepo.save).toHaveBeenCalled();
    });

    it('should update existing account config', async () => {
      const existing = makeEntity({ accountId });
      mockConfigRepo.findOne.mockResolvedValue(existing);

      await service.updateAccountConfig(accountId, {
        equityDriftThreshold: 100,
      });

      expect(mockConfigRepo.save).toHaveBeenCalledWith(
        expect.objectContaining({ equityDriftThreshold: '100' }),
      );
    });

    it('should invalidate account Redis cache after update', async () => {
      mockConfigRepo.findOne.mockResolvedValue(null);

      await service.updateAccountConfig(accountId, {
        reconciliationIntervalSeconds: 60,
      });

      expect(mockRedis.del).toHaveBeenCalledWith(
        `reconciliation:config:${accountId}`,
      );
    });

    it('should reject invalid config', async () => {
      await expect(
        service.updateAccountConfig(accountId, { balanceDriftThreshold: -1 }),
      ).rejects.toThrow(BadRequestException);
    });

    it('should only update provided fields', async () => {
      const existing = makeEntity({
        accountId,
        reconciliationIntervalSeconds: 120,
        balanceDriftThreshold: '15.00',
      });
      mockConfigRepo.findOne.mockResolvedValue(existing);

      await service.updateAccountConfig(accountId, {
        equityDriftThreshold: 75,
      });

      const savedArg = mockConfigRepo.save.mock.calls[0][0];
      expect(savedArg.reconciliationIntervalSeconds).toBe(120);
      expect(savedArg.balanceDriftThreshold).toBe('15.00');
      expect(savedArg.equityDriftThreshold).toBe('75');
    });
  });
});
