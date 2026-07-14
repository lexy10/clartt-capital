import {
  Inject,
  Injectable,
  ConflictException,
  NotFoundException,
  ForbiddenException,
  BadGatewayException,
  HttpException,
  HttpStatus,
  InternalServerErrorException,
  Logger,
} from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { HttpService } from '@nestjs/axios';
import { firstValueFrom } from 'rxjs';
import Redis from 'ioredis';
import { REDIS_CLIENT } from '../../common/modules/redis.module';
import { In } from 'typeorm';
import { TradingAccount } from '../trades/entities/trading-account.entity';
import { PortfolioSnapshot } from '../portfolios/entities/portfolio-snapshot.entity';
import { AccountStrategy } from './entities/account-strategy.entity';
import { CreateAccountDto } from './dto/create-account.dto';
import { InstrumentsService } from '../instruments/instruments.service';
import { BackfillService } from '../market-data/backfill.service';
import { CircuitBreaker } from '../../common/circuit-breaker/circuit-breaker';
import { EXECUTION_ENGINE_CIRCUIT_BREAKER } from '../../common/circuit-breaker/circuit-breaker.module';

@Injectable()
export class AccountsService {
  private readonly logger = new Logger(AccountsService.name);
  private readonly engineBaseUrl: string;

  constructor(
    @InjectRepository(TradingAccount)
    private readonly tradingAccountRepo: Repository<TradingAccount>,
    @InjectRepository(PortfolioSnapshot)
    private readonly snapshotRepo: Repository<PortfolioSnapshot>,
    @InjectRepository(AccountStrategy)
    private readonly accountStrategyRepo: Repository<AccountStrategy>,
    private readonly httpService: HttpService,
    private readonly instrumentsService: InstrumentsService,
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
    private readonly backfillService: BackfillService,
    @Inject(EXECUTION_ENGINE_CIRCUIT_BREAKER) private readonly circuitBreaker: CircuitBreaker,
  ) {
    this.engineBaseUrl =
      process.env.EXECUTION_ENGINE_URL || 'http://execution-engine:8002';
  }

  /** Strip secrets before an account entity leaves the API boundary.
   *  The broker token must never reach the browser: the dashboard doesn't
   *  need it, and an XSS there must not become "attacker can trade". */
  static sanitize(account: TradingAccount): Omit<TradingAccount, 'derivApiToken'> {
    const { derivApiToken: _token, ...safe } = account;
    return safe as Omit<TradingAccount, 'derivApiToken'>;
  }

  async create(
    userId: string,
    dto: CreateAccountDto,
  ): Promise<TradingAccount> {
    // Route by brokerProvider — default "metaapi" preserves legacy behavior
    const brokerProvider = dto.brokerProvider || 'metaapi';

    if (brokerProvider === 'deriv') {
      return this.createDerivDirectAccount(userId, dto);
    }
    return this.createMetaapiAccount(userId, dto);
  }

  /**
   * Create a Deriv-direct account. No MetaAPI provisioning — just stores the
   * Deriv API token + login ID. The execution engine's DerivSyntheticClient
   * will use these to authorize WebSocket trades.
   */
  private async createDerivDirectAccount(
    userId: string,
    dto: CreateAccountDto,
  ): Promise<TradingAccount> {
    if (!dto.derivApiToken || !dto.derivLoginId) {
      throw new BadGatewayException(
        'Deriv-direct accounts require derivApiToken and derivLoginId',
      );
    }

    const existing = await this.tradingAccountRepo.findOne({
      where: {
        userId,
        derivLoginId: dto.derivLoginId,
        isActive: true,
      },
    });
    if (existing) {
      throw new ConflictException(
        `Deriv account with login ID ${dto.derivLoginId} already exists`,
      );
    }

    const account = this.tradingAccountRepo.create({
      userId,
      metaapiAccountId: null,
      label: dto.label || `Deriv ${dto.derivLoginId}`,
      mt5Login: null,
      mt5Server: null,
      isActive: true,
      brokerProvider: 'deriv',
      accountKind: dto.accountKind || 'personal',
      derivApiToken: dto.derivApiToken,
      derivLoginId: dto.derivLoginId,
    });
    const savedAccount = await this.tradingAccountRepo.save(account);

    // Auto-associate synthetic instruments and sync to Redis
    await this.instrumentsService.autoAssociateDefaults(savedAccount.id);
    await this.syncAccountBrokerSymbolsToRedis(savedAccount.id);

    this.logger.log(
      `Created Deriv-direct account ${savedAccount.id} (login=${dto.derivLoginId})`,
    );
    return savedAccount;
  }

  /** Legacy MetaAPI-provisioned account creation. */
  private async createMetaapiAccount(
    userId: string,
    dto: CreateAccountDto,
  ): Promise<TradingAccount> {
    if (!dto.login || !dto.password || !dto.serverName || !dto.platform) {
      throw new BadGatewayException(
        'MetaAPI accounts require login, password, serverName, and platform',
      );
    }

    const existing = await this.tradingAccountRepo.findOne({
      where: {
        userId,
        mt5Login: dto.login,
        mt5Server: dto.serverName,
        isActive: true,
      },
    });

    if (existing) {
      throw new ConflictException(
        'Account with this login and server already exists',
      );
    }

    let provisionResponse: { metaapi_account_id: string; state: string };
    try {
      provisionResponse = await this.circuitBreaker.execute(
        async () => {
          const { data } = await firstValueFrom(
            this.httpService.post(`${this.engineBaseUrl}/accounts/provision`, {
              login: dto.login,
              password: dto.password,
              server: dto.serverName,
              platform: dto.platform,
            }),
          );
          return data;
        },
        () => {
          throw new HttpException('Execution Engine is unavailable', HttpStatus.SERVICE_UNAVAILABLE);
        },
      );
    } catch (error) {
      if (error instanceof HttpException && error.getStatus() === HttpStatus.SERVICE_UNAVAILABLE) {
        throw error;
      }
      this.logger.error('Execution engine provisioning failed', error?.response?.data || error);
      const status = error?.response?.status;
      const detail = error?.response?.data?.detail;
      if (status === 400) {
        throw new BadGatewayException(detail || 'MetaAPI validation failed');
      }
      if (status === 504) {
        throw new BadGatewayException(detail || 'Account deployment timed out');
      }
      throw new BadGatewayException(detail || 'Execution engine unavailable');
    }

    const account = this.tradingAccountRepo.create({
      userId,
      metaapiAccountId: provisionResponse.metaapi_account_id,
      label: dto.label || dto.login,
      mt5Login: dto.login,
      mt5Server: dto.serverName,
      isActive: true,
      brokerProvider: 'metaapi',
      accountKind: dto.accountKind || 'personal',
    });

    const savedAccount = await this.tradingAccountRepo.save(account);

    // Auto-associate all active instruments with canonical symbols as defaults
    await this.instrumentsService.autoAssociateDefaults(savedAccount.id);
    await this.syncAccountBrokerSymbolsToRedis(savedAccount.id);

    // Fire-and-forget backfill — don't await, errors handled internally
    this.backfillService.triggerBackfill().catch((err) => {
      this.logger.error(
        `Backfill trigger failed: ${err.message}`,
      );
    });

    return savedAccount;
  }

  async findAllByUser(userId: string): Promise<TradingAccount[]> {
    return this.tradingAccountRepo.find({
      where: { userId, isActive: true },
    });
  }

  async findAllActive(): Promise<TradingAccount[]> {
    return this.tradingAccountRepo.find({ where: { isActive: true } });
  }


  async getStatus(
      userId: string,
      accountId: string,
    ): Promise<{ state: string; connection_status: string }> {
      const account = await this.findOwnedAccount(userId, accountId);

      // Try Redis cache first
      try {
        const cached = await this.redis.get(`account:status:${accountId}`);
        if (cached) {
          const parsed = JSON.parse(cached);
          return { state: parsed.state, connection_status: parsed.connection_status };
        }
      } catch (err) {
        this.logger.warn(`Redis cache read failed for account status ${accountId}: ${err.message}`);
      }

      // Route by broker provider — Deriv-direct accounts don't go through MetaAPI
      if (account.brokerProvider === 'deriv') {
        return this.getDerivStatus(account);
      }

      // Default: MetaAPI flow (legacy)
      if (!account.metaapiAccountId) {
        // Account is marked as MetaAPI but has no ID — return DISCONNECTED gracefully
        return { state: 'UNKNOWN', connection_status: 'DISCONNECTED' };
      }

      try {
        const { data } = await this.circuitBreaker.execute(
          () => firstValueFrom(
            this.httpService.get(
              `${this.engineBaseUrl}/accounts/${account.metaapiAccountId}/status`,
            ),
          ),
          () => {
            throw new HttpException('Execution Engine is unavailable', HttpStatus.SERVICE_UNAVAILABLE);
          },
        );
        return { state: data.state, connection_status: data.connection_status };
      } catch (error) {
        if (error instanceof HttpException && error.getStatus() === HttpStatus.SERVICE_UNAVAILABLE) {
          throw error;
        }
        const statusCode = error?.response?.status;
        if (statusCode === 404) {
          throw new NotFoundException('MetaApi account not found');
        }
        this.logger.error('Execution engine status fetch failed', error);
        throw new BadGatewayException('Execution engine unavailable');
      }
    }

  /**
   * Get status for a Deriv-direct account. Calls the execution engine's
   * Deriv status endpoint which authorizes with the account's stored token
   * and returns live balance + connection state.
   */
  private async getDerivStatus(
    account: TradingAccount,
  ): Promise<{ state: string; connection_status: string }> {
    if (!account.derivLoginId || !account.derivApiToken) {
      return { state: 'UNCONFIGURED', connection_status: 'DISCONNECTED' };
    }
    try {
      const { data } = await this.circuitBreaker.execute(
        () => firstValueFrom(
          this.httpService.post(
            `${this.engineBaseUrl}/accounts/deriv/status`,
            {
              login_id: account.derivLoginId,
              api_token: account.derivApiToken,
            },
            { timeout: 8000 },
          ),
        ),
        () => {
          throw new HttpException('Execution Engine is unavailable', HttpStatus.SERVICE_UNAVAILABLE);
        },
      );
      return {
        state: data?.state ?? 'DEPLOYED',
        connection_status: data?.connection_status ?? 'CONNECTED',
      };
    } catch (error) {
      if (error instanceof HttpException && error.getStatus() === HttpStatus.SERVICE_UNAVAILABLE) {
        throw error;
      }
      this.logger.warn(`Deriv status fetch failed for ${account.id}: ${(error as Error).message}`);
      // If the network/API check fails, return a sensible default rather than throwing —
      // the dashboard shouldn't crash because of an upstream issue.
      return { state: 'UNKNOWN', connection_status: 'DISCONNECTED' };
    }
  }

  async getDetails(
      userId: string,
      accountId: string,
    ): Promise<Record<string, unknown>> {
      const account = await this.findOwnedAccount(userId, accountId);

      // Get status (routes by broker provider)
      let state = 'UNKNOWN';
      let connectionStatus = 'UNKNOWN';
      try {
        const status = await this.getStatus(userId, accountId);
        state = status.state;
        connectionStatus = status.connection_status;
      } catch (err) {
        this.logger.warn(`Status fetch failed for ${accountId}: ${(err as Error).message}`);
      }

      // For Deriv-direct accounts, fetch live balance from the execution engine
      // (which authorizes with the per-account token).
      if (account.brokerProvider === 'deriv' && account.derivApiToken && account.derivLoginId) {
        try {
          const { data } = await this.circuitBreaker.execute(
            () => firstValueFrom(
              this.httpService.post(
                `${this.engineBaseUrl}/accounts/deriv/details`,
                {
                  login_id: account.derivLoginId,
                  api_token: account.derivApiToken,
                },
                { timeout: 8000 },
              ),
            ),
            () => {
              throw new HttpException('Execution Engine is unavailable', HttpStatus.SERVICE_UNAVAILABLE);
            },
          );
          const instruments =
            await this.instrumentsService.getAccountInstruments(accountId);
          return {
            state,
            connection_status: connectionStatus,
            balance: data?.balance ?? 0,
            equity: data?.equity ?? data?.balance ?? 0,
            margin: 0,
            free_margin: data?.balance ?? 0,
            open_positions: data?.open_positions ?? 0,
            leverage: 0,
            currency: data?.currency ?? 'USD',
            login_id: account.derivLoginId,
            broker_provider: 'deriv',
            instruments,
          };
        } catch (err) {
          this.logger.warn(
            `Deriv details fetch failed for ${accountId}, falling back to snapshot: ${(err as Error).message}`,
          );
        }
      }

      // Default: latest snapshot from DB (MetaAPI accounts use the portfolio snapshot worker)
      const latestSnapshot = await this.snapshotRepo.findOne({
        where: { accountId },
        order: { snapshotAt: 'DESC' },
      });

      const instruments =
        await this.instrumentsService.getAccountInstruments(accountId);

      return {
        state,
        connection_status: connectionStatus,
        balance: latestSnapshot ? parseFloat(latestSnapshot.balance) : 0,
        equity: latestSnapshot ? parseFloat(latestSnapshot.equity) : 0,
        margin: latestSnapshot ? parseFloat(latestSnapshot.margin) : 0,
        free_margin: latestSnapshot ? parseFloat(latestSnapshot.freeMargin) : 0,
        open_positions: latestSnapshot ? latestSnapshot.openPositions : 0,
        leverage: latestSnapshot ? latestSnapshot.leverage : 0,
        broker_provider: account.brokerProvider,
        instruments,
      };
    }

  async updateLabel(
    userId: string,
    accountId: string,
    label: string,
  ): Promise<TradingAccount> {
    const account = await this.findOwnedAccount(userId, accountId);
    account.label = label;
    return this.tradingAccountRepo.save(account);
  }

  /** Replace a Deriv account's API token (and optionally its login ID) so an
   *  expired/invalid token can be fixed in place. The token is re-encrypted at
   *  rest by the entity transformer. It's read fresh from the DB whenever a
   *  worker starts or status/details are fetched, so the new token takes effect
   *  immediately for status checks and on the next autopilot (re)start. */
  async updateDerivToken(
    userId: string,
    accountId: string,
    derivApiToken: string,
    derivLoginId?: string,
  ): Promise<TradingAccount> {
    const account = await this.findOwnedAccount(userId, accountId);
    if (account.brokerProvider !== 'deriv') {
      throw new BadGatewayException('Not a Deriv-direct account');
    }
    account.derivApiToken = derivApiToken;
    if (derivLoginId) {
      account.derivLoginId = derivLoginId;
    }
    // Invalidate the cached status so the next fetch re-authorizes with the
    // new token instead of returning the stale DISCONNECTED value.
    try {
      await this.redis.del(`account:status:${accountId}`);
    } catch { /* non-fatal */ }
    return this.tradingAccountRepo.save(account);
  }

  /**
   * Trigger a reconciliation sweep for this account against the broker.
   *
   * For Deriv: forwards to the execution engine's
   * `/reconciliation/deriv/sweep` endpoint with the account's stored token.
   * The engine pulls each "still-open in our DB" trade, looks up its close
   * data at Deriv (proposal_open_contract → profit_table fallback), and
   * writes exit_price + profit_loss + closed_at back to the trades row.
   *
   * For other brokers: not yet implemented — returns an empty result.
   */
  async reconcile(
    userId: string,
    accountId: string,
    lookbackHours = 168,
    refreshClosedHours = 0,
  ): Promise<Record<string, unknown>> {
    const account = await this.findOwnedAccount(userId, accountId);

    if (account.brokerProvider !== 'deriv') {
      return {
        account_id: accountId,
        broker_provider: account.brokerProvider,
        candidates: 0,
        updated: 0,
        still_open: 0,
        failed: 0,
        note: 'Reconciliation for this broker is not yet implemented.',
      };
    }
    if (!account.derivLoginId || !account.derivApiToken) {
      throw new HttpException(
        'Account is missing Deriv credentials',
        HttpStatus.BAD_REQUEST,
      );
    }
    try {
      const { data } = await this.circuitBreaker.execute(
        () => firstValueFrom(
          this.httpService.post(
            `${this.engineBaseUrl}/reconciliation/deriv/sweep`,
            {
              account_id: accountId,
              deriv_api_token: account.derivApiToken,
              deriv_login_id: account.derivLoginId,
              lookback_hours: lookbackHours,
              refresh_closed_hours: refreshClosedHours,
            },
            { timeout: 60_000 },
          ),
        ),
        () => {
          throw new HttpException(
            'Execution Engine is unavailable',
            HttpStatus.SERVICE_UNAVAILABLE,
          );
        },
      );
      this.logger.log(
        `Reconciliation for ${accountId}: updated=${data?.updated ?? 0}, ` +
        `still_open=${data?.still_open ?? 0}, failed=${data?.failed ?? 0}`,
      );
      return data ?? {};
    } catch (err) {
      if (err instanceof HttpException) throw err;
      this.logger.error(
        `Reconciliation failed for ${accountId}: ${(err as Error).message}`,
      );
      throw new HttpException(
        `Reconciliation failed: ${(err as Error).message}`,
        HttpStatus.INTERNAL_SERVER_ERROR,
      );
    }
  }

  async deploy(userId: string, accountId: string): Promise<void> {
    const account = await this.findOwnedAccount(userId, accountId);

    try {
      await this.circuitBreaker.execute(
        () => firstValueFrom(
          this.httpService.post(
            `${this.engineBaseUrl}/accounts/${account.metaapiAccountId}/deploy`,
          ),
        ),
        () => {
          throw new HttpException('Execution Engine is unavailable', HttpStatus.SERVICE_UNAVAILABLE);
        },
      );
    } catch (error) {
      if (error instanceof HttpException && error.getStatus() === HttpStatus.SERVICE_UNAVAILABLE) {
        throw error;
      }
      const status = error?.response?.status;
      if (status === 404) {
        throw new BadGatewayException('MetaApi account not found');
      }
      this.logger.error('Execution engine deploy failed', error);
      throw new InternalServerErrorException('Failed to deploy account');
    }

    // Fire-and-forget backfill — don't await, errors handled internally
    this.backfillService.triggerBackfill().catch((err) => {
      this.logger.error(
        `Backfill trigger failed: ${err.message}`,
      );
    });
  }

  async undeploy(userId: string, accountId: string): Promise<void> {
    const account = await this.findOwnedAccount(userId, accountId);

    // Stop candle streaming before undeploy
    this.backfillService.stopStream().catch((err) => {
      this.logger.error(`Stream stop failed: ${err.message}`);
    });

    try {
      await this.circuitBreaker.execute(
        () => firstValueFrom(
          this.httpService.post(
            `${this.engineBaseUrl}/accounts/${account.metaapiAccountId}/undeploy`,
          ),
        ),
        () => {
          throw new HttpException('Execution Engine is unavailable', HttpStatus.SERVICE_UNAVAILABLE);
        },
      );
    } catch (error) {
      if (error instanceof HttpException && error.getStatus() === HttpStatus.SERVICE_UNAVAILABLE) {
        throw error;
      }
      const status = error?.response?.status;
      if (status === 404) {
        throw new BadGatewayException('MetaApi account not found');
      }
      this.logger.error('Execution engine undeploy failed', error);
      throw new InternalServerErrorException('Failed to undeploy account');
    }
  }

  async remove(userId: string, accountId: string): Promise<void> {
    const account = await this.findOwnedAccount(userId, accountId);

    // Stop candle streaming before remove
    this.backfillService.stopStream().catch((err) => {
      this.logger.error(`Stream stop failed: ${err.message}`);
    });

    // Delete from MetaAPI (undeploy + remove). 404 = already gone, safe to proceed.
    try {
      await this.circuitBreaker.execute(
        () => firstValueFrom(
          this.httpService.post(
            `${this.engineBaseUrl}/accounts/${account.metaapiAccountId}/remove`,
          ),
        ),
        () => {
          throw new HttpException('Execution Engine is unavailable', HttpStatus.SERVICE_UNAVAILABLE);
        },
      );
    } catch (error) {
      if (error instanceof HttpException && error.getStatus() === HttpStatus.SERVICE_UNAVAILABLE) {
        throw error;
      }
      const status = error?.response?.status;
      if (status !== 404) {
        this.logger.error('Execution engine remove failed', error);
        throw new InternalServerErrorException('Failed to remove account from MetaAPI');
      }
      this.logger.warn(
        `MetaApi account ${account.metaapiAccountId} not found remotely, proceeding with local removal`,
      );
    }

    // Nullify FK references that use NO ACTION (trades, positions, portfolio_snapshots)
    await this.tradingAccountRepo.manager.query(
      `UPDATE trades SET account_id = NULL WHERE account_id = $1`,
      [accountId],
    );
    await this.tradingAccountRepo.manager.query(
      `UPDATE positions SET account_id = NULL WHERE account_id = $1`,
      [accountId],
    );
    await this.tradingAccountRepo.manager.query(
      `DELETE FROM portfolio_snapshots WHERE account_id = $1`,
      [accountId],
    );

    // CASCADE handles: account_instruments, autopilot_states
    await this.tradingAccountRepo.remove(account);
  }

  async getBrokerSymbols(userId: string, accountId: string): Promise<string[]> {
    const account = await this.findOwnedAccount(userId, accountId);

    try {
      const { data } = await this.circuitBreaker.execute(
        () => firstValueFrom(
          this.httpService.get(
            `${this.engineBaseUrl}/accounts/${account.metaapiAccountId}/symbols`,
            { timeout: 120000 },
          ),
        ),
        () => {
          throw new HttpException('Execution Engine is unavailable', HttpStatus.SERVICE_UNAVAILABLE);
        },
      );
      return data.symbols;
    } catch (error) {
      if (error instanceof HttpException && error.getStatus() === HttpStatus.SERVICE_UNAVAILABLE) {
        throw error;
      }
      if (error?.code === 'ECONNABORTED') {
        this.logger.warn('Broker symbols fetch timed out for account %s', accountId);
        return [];
      }
      const status = error?.response?.status;
      // 504 = sync timeout in execution engine — return empty so dashboard can retry
      if (status === 504) {
        this.logger.warn('Broker sync timed out for account %s (504)', accountId);
        return [];
      }
      this.logger.error('Execution engine broker symbols fetch failed', error);
      throw new BadGatewayException('Execution engine unavailable');
    }
  }


  async getAccountStrategies(userId: string, accountId: string): Promise<AccountStrategy[]> {
    await this.findOwnedAccount(userId, accountId);
    return this.accountStrategyRepo.find({
      where: { accountId },
      relations: ['strategy'],
      order: { createdAt: 'ASC' },
    });
  }

  async setAccountStrategies(userId: string, accountId: string, strategyIds: string[]): Promise<AccountStrategy[]> {
    await this.findOwnedAccount(userId, accountId);

    // Remove existing associations
    await this.accountStrategyRepo.delete({ accountId });

    if (strategyIds.length === 0) {
      // Sync empty list to Redis
      await this.syncAccountStrategyIdsToRedis(accountId, []);
      return [];
    }

    // Create new associations
    const entities = strategyIds.map((strategyId) =>
      this.accountStrategyRepo.create({ accountId, strategyId }),
    );
    await this.accountStrategyRepo.save(entities);

    // Sync to Redis for execution engine fast reads
    await this.syncAccountStrategyIdsToRedis(accountId, strategyIds);

    return this.accountStrategyRepo.find({
      where: { accountId },
      relations: ['strategy'],
      order: { createdAt: 'ASC' },
    });
  }

  /**
   * Sync account-strategy mapping to Redis and publish change notification.
   * Follows the same dual-write pattern as AutopilotService.
   */
  private async syncAccountStrategyIdsToRedis(accountId: string, strategyIds: string[]): Promise<void> {
    const redisKey = `account:strategies:${accountId}`;
    const channel = 'account:strategies:channel';
    try {
      await this.redis.set(redisKey, JSON.stringify(strategyIds));
      await this.redis.publish(channel, JSON.stringify({ accountId, strategyIds }));
      this.logger.log(`Synced ${strategyIds.length} strategy IDs to Redis for account ${accountId}`);
    } catch (err) {
      this.logger.error(`Failed to sync account strategies to Redis for ${accountId}: ${(err as Error).message}`);
    }
  }

  /**
   * Sync all account-strategy mappings to Redis on startup.
   * Called by AccountsModule.onModuleInit().
   */
  async syncAllAccountStrategiesToRedis(): Promise<void> {
    const allAssociations = await this.accountStrategyRepo.find();
    const byAccount = new Map<string, string[]>();
    for (const assoc of allAssociations) {
      const list = byAccount.get(assoc.accountId) || [];
      list.push(assoc.strategyId);
      byAccount.set(assoc.accountId, list);
    }
    for (const [accountId, strategyIds] of byAccount) {
      try {
        await this.redis.set(`account:strategies:${accountId}`, JSON.stringify(strategyIds));
      } catch (err) {
        this.logger.error(`Failed to sync strategies for account ${accountId}: ${(err as Error).message}`);
      }
    }
    this.logger.log(`Synced account-strategy mappings for ${byAccount.size} accounts to Redis`);
  }

  /**
   * Sync account broker symbol mappings to Redis.
   * Key: account:symbols:{accountId} → { "R_75": "Volatility 75 Index", ... }
   */
  private async syncAccountBrokerSymbolsToRedis(accountId: string): Promise<void> {
    const redisKey = `account:symbols:${accountId}`;
    try {
      const mappings = await this.instrumentsService.getAccountInstruments(accountId);
      const symbolMap: Record<string, string> = {};
      for (const m of mappings) {
        if (m.instrument) {
          symbolMap[m.instrument.symbol] = m.brokerSymbol;
        }
      }
      await this.redis.set(redisKey, JSON.stringify(symbolMap));
      this.logger.log(`Synced ${Object.keys(symbolMap).length} broker symbol mappings to Redis for account ${accountId}`);
    } catch (err) {
      this.logger.error(`Failed to sync broker symbols to Redis for ${accountId}: ${(err as Error).message}`);
    }
  }

  /**
   * Set account instrument mappings and sync broker symbols to Redis.
   */
  async setAccountInstruments(
    userId: string,
    accountId: string,
    items: import('../instruments/dto/set-account-instruments.dto').AccountInstrumentItemDto[],
  ): Promise<import('../instruments/entities/account-instrument.entity').AccountInstrument[]> {
    await this.findOwnedAccount(userId, accountId);
    const result = await this.instrumentsService.setAccountInstruments(accountId, items);
    await this.syncAccountBrokerSymbolsToRedis(accountId);
    return result;
  }

  /**
   * Sync all account broker symbol mappings to Redis on startup.
   */
  async syncAllAccountBrokerSymbolsToRedis(): Promise<void> {
    const accounts = await this.tradingAccountRepo.find({ where: { isActive: true } });
    for (const account of accounts) {
      await this.syncAccountBrokerSymbolsToRedis(account.id);
    }
    this.logger.log(`Synced broker symbol mappings for ${accounts.length} accounts to Redis`);
  }

  private async findOwnedAccount(
    userId: string,
    accountId: string,
  ): Promise<TradingAccount> {
    const account = await this.tradingAccountRepo.findOne({
      where: { id: accountId },
    });

    if (!account) {
      throw new NotFoundException('Trading account not found');
    }

    if (account.userId !== userId) {
      throw new ForbiddenException('Not authorized to access this account');
    }

    return account;
  }
}
