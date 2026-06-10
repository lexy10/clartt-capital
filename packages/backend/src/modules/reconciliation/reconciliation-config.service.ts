import {
  Inject,
  Injectable,
  BadRequestException,
  Logger,
} from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import Redis from 'ioredis';
import { REDIS_CLIENT } from '../../common/modules/redis.module';
import { ReconciliationConfig } from './entities/reconciliation-config.entity';
import { EffectiveConfig } from './types';

export interface UpdateConfigDto {
  reconciliationIntervalSeconds?: number;
  balanceDriftThreshold?: number;
  equityDriftThreshold?: number;
  positionSizeDriftThreshold?: number;
  autoCorrectPhantomPositions?: boolean;
  autoCorrectMissingPositions?: boolean;
  autoCorrectBalanceDrift?: boolean;
  escalationCycleCount?: number;
}

const GLOBAL_CACHE_KEY = 'reconciliation:config:global';
const ACCOUNT_CACHE_KEY_PREFIX = 'reconciliation:config:';

const DEFAULT_CONFIG: EffectiveConfig = {
  reconciliationIntervalSeconds: 60,
  balanceDriftThreshold: 10,
  equityDriftThreshold: 50,
  positionSizeDriftThreshold: 0.01,
  autoCorrectPhantomPositions: false,
  autoCorrectMissingPositions: false,
  autoCorrectBalanceDrift: false,
  escalationCycleCount: 3,
};

@Injectable()
export class ReconciliationConfigService {
  private readonly logger = new Logger(ReconciliationConfigService.name);

  constructor(
    @InjectRepository(ReconciliationConfig)
    private readonly configRepo: Repository<ReconciliationConfig>,
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
  ) {}

  /**
   * Get the effective config for an account: per-account overrides global defaults.
   * Checks Redis cache first, falls back to PostgreSQL, then hardcoded defaults.
   */
  async getEffectiveConfig(accountId: string): Promise<EffectiveConfig> {
    // Try account-specific cache first
    const accountCacheKey = `${ACCOUNT_CACHE_KEY_PREFIX}${accountId}`;
    try {
      const cached = await this.redis.get(accountCacheKey);
      if (cached) {
        return JSON.parse(cached) as EffectiveConfig;
      }
    } catch (err) {
      this.logger.warn(`Redis cache read failed for account ${accountId}: ${err}`);
    }

    // Load global config
    const globalConfig = await this.loadGlobalConfig();

    // Load per-account config from DB
    let accountConfig: ReconciliationConfig | null = null;
    try {
      accountConfig = await this.configRepo.findOne({
        where: { accountId },
      });
    } catch (err) {
      this.logger.warn(`Failed to load account config from DB for ${accountId}: ${err}`);
    }

    // Merge: per-account overrides global
    const effective = this.mergeConfigs(globalConfig, accountConfig);

    // Cache the effective config
    try {
      await this.redis.set(accountCacheKey, JSON.stringify(effective));
    } catch (err) {
      this.logger.warn(`Redis cache write failed for account ${accountId}: ${err}`);
    }

    return effective;
  }

  /**
   * Update the global config (accountId = null). Persists to PostgreSQL and invalidates Redis cache.
   */
  async updateGlobalConfig(dto: UpdateConfigDto): Promise<ReconciliationConfig> {
    this.validateConfig(dto);

    let config = await this.configRepo.findOne({
      where: { accountId: null as unknown as string },
    });

    if (!config) {
      config = this.configRepo.create({ accountId: null });
    }

    this.applyDtoToEntity(config, dto);
    const saved = await this.configRepo.save(config);

    // Invalidate global cache and all account caches (they depend on global)
    try {
      await this.redis.del(GLOBAL_CACHE_KEY);
      await this.invalidateAllAccountCaches();
    } catch (err) {
      this.logger.warn(`Redis cache invalidation failed after global config update: ${err}`);
    }

    return saved;
  }

  /**
   * Update per-account config. Persists to PostgreSQL and invalidates Redis cache.
   */
  async updateAccountConfig(
    accountId: string,
    dto: UpdateConfigDto,
  ): Promise<ReconciliationConfig> {
    this.validateConfig(dto);

    let config = await this.configRepo.findOne({
      where: { accountId },
    });

    if (!config) {
      config = this.configRepo.create({ accountId });
    }

    this.applyDtoToEntity(config, dto);
    const saved = await this.configRepo.save(config);

    // Invalidate account-specific cache
    try {
      await this.redis.del(`${ACCOUNT_CACHE_KEY_PREFIX}${accountId}`);
    } catch (err) {
      this.logger.warn(`Redis cache invalidation failed for account ${accountId}: ${err}`);
    }

    return saved;
  }

  /**
   * Validate config values. Throws BadRequestException on invalid input.
   * - interval must be >= 30 seconds
   * - all thresholds must be > 0
   */
  validateConfig(dto: UpdateConfigDto): void {
    const errors: string[] = [];

    if (
      dto.reconciliationIntervalSeconds !== undefined &&
      dto.reconciliationIntervalSeconds < 30
    ) {
      errors.push(
        'reconciliationIntervalSeconds must be >= 30 seconds',
      );
    }

    if (
      dto.balanceDriftThreshold !== undefined &&
      dto.balanceDriftThreshold <= 0
    ) {
      errors.push('balanceDriftThreshold must be > 0');
    }

    if (
      dto.equityDriftThreshold !== undefined &&
      dto.equityDriftThreshold <= 0
    ) {
      errors.push('equityDriftThreshold must be > 0');
    }

    if (
      dto.positionSizeDriftThreshold !== undefined &&
      dto.positionSizeDriftThreshold <= 0
    ) {
      errors.push('positionSizeDriftThreshold must be > 0');
    }

    if (
      dto.escalationCycleCount !== undefined &&
      dto.escalationCycleCount <= 0
    ) {
      errors.push('escalationCycleCount must be > 0');
    }

    if (errors.length > 0) {
      throw new BadRequestException(errors.join('; '));
    }
  }

  /**
   * Load global config: Redis cache → PostgreSQL → hardcoded defaults.
   */
  private async loadGlobalConfig(): Promise<EffectiveConfig> {
    // Try Redis cache
    try {
      const cached = await this.redis.get(GLOBAL_CACHE_KEY);
      if (cached) {
        return JSON.parse(cached) as EffectiveConfig;
      }
    } catch (err) {
      this.logger.warn(`Redis cache read failed for global config: ${err}`);
    }

    // Try PostgreSQL
    let dbConfig: ReconciliationConfig | null = null;
    try {
      dbConfig = await this.configRepo.findOne({
        where: { accountId: null as unknown as string },
      });
    } catch (err) {
      this.logger.warn(`Failed to load global config from DB: ${err}`);
    }

    const effective = this.entityToEffectiveConfig(dbConfig);

    // Cache global config
    try {
      await this.redis.set(GLOBAL_CACHE_KEY, JSON.stringify(effective));
    } catch (err) {
      this.logger.warn(`Redis cache write failed for global config: ${err}`);
    }

    return effective;
  }

  /**
   * Convert a DB entity (or null) to EffectiveConfig, using defaults for missing values.
   */
  private entityToEffectiveConfig(
    entity: ReconciliationConfig | null,
  ): EffectiveConfig {
    if (!entity) {
      return { ...DEFAULT_CONFIG };
    }

    return {
      reconciliationIntervalSeconds: entity.reconciliationIntervalSeconds,
      balanceDriftThreshold: parseFloat(entity.balanceDriftThreshold),
      equityDriftThreshold: parseFloat(entity.equityDriftThreshold),
      positionSizeDriftThreshold: parseFloat(entity.positionSizeDriftThreshold),
      autoCorrectPhantomPositions: entity.autoCorrectPhantomPositions,
      autoCorrectMissingPositions: entity.autoCorrectMissingPositions,
      autoCorrectBalanceDrift: entity.autoCorrectBalanceDrift,
      escalationCycleCount: entity.escalationCycleCount,
    };
  }

  /**
   * Merge global config with per-account overrides.
   * Per-account entity fields override global defaults when the account config exists.
   */
  private mergeConfigs(
    globalConfig: EffectiveConfig,
    accountEntity: ReconciliationConfig | null,
  ): EffectiveConfig {
    if (!accountEntity) {
      return { ...globalConfig };
    }

    // Per-account config overrides all fields from the entity
    return {
      reconciliationIntervalSeconds: accountEntity.reconciliationIntervalSeconds,
      balanceDriftThreshold: parseFloat(accountEntity.balanceDriftThreshold),
      equityDriftThreshold: parseFloat(accountEntity.equityDriftThreshold),
      positionSizeDriftThreshold: parseFloat(accountEntity.positionSizeDriftThreshold),
      autoCorrectPhantomPositions: accountEntity.autoCorrectPhantomPositions,
      autoCorrectMissingPositions: accountEntity.autoCorrectMissingPositions,
      autoCorrectBalanceDrift: accountEntity.autoCorrectBalanceDrift,
      escalationCycleCount: accountEntity.escalationCycleCount,
    };
  }

  /**
   * Apply DTO fields to a config entity (only set fields that are provided).
   */
  private applyDtoToEntity(
    entity: ReconciliationConfig,
    dto: UpdateConfigDto,
  ): void {
    if (dto.reconciliationIntervalSeconds !== undefined) {
      entity.reconciliationIntervalSeconds = dto.reconciliationIntervalSeconds;
    }
    if (dto.balanceDriftThreshold !== undefined) {
      entity.balanceDriftThreshold = String(dto.balanceDriftThreshold);
    }
    if (dto.equityDriftThreshold !== undefined) {
      entity.equityDriftThreshold = String(dto.equityDriftThreshold);
    }
    if (dto.positionSizeDriftThreshold !== undefined) {
      entity.positionSizeDriftThreshold = String(dto.positionSizeDriftThreshold);
    }
    if (dto.autoCorrectPhantomPositions !== undefined) {
      entity.autoCorrectPhantomPositions = dto.autoCorrectPhantomPositions;
    }
    if (dto.autoCorrectMissingPositions !== undefined) {
      entity.autoCorrectMissingPositions = dto.autoCorrectMissingPositions;
    }
    if (dto.autoCorrectBalanceDrift !== undefined) {
      entity.autoCorrectBalanceDrift = dto.autoCorrectBalanceDrift;
    }
    if (dto.escalationCycleCount !== undefined) {
      entity.escalationCycleCount = dto.escalationCycleCount;
    }
  }

  /**
   * Invalidate all account-specific config caches.
   * Uses SCAN to find and delete matching keys.
   */
  private async invalidateAllAccountCaches(): Promise<void> {
    const pattern = `${ACCOUNT_CACHE_KEY_PREFIX}*`;
    let cursor = '0';
    do {
      const [nextCursor, keys] = await this.redis.scan(
        cursor,
        'MATCH',
        pattern,
        'COUNT',
        100,
      );
      cursor = nextCursor;
      if (keys.length > 0) {
        // Filter out the global key
        const accountKeys = keys.filter((k) => k !== GLOBAL_CACHE_KEY);
        if (accountKeys.length > 0) {
          await this.redis.del(...accountKeys);
        }
      }
    } while (cursor !== '0');
  }
}
