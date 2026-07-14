import {
  Injectable,
  UnauthorizedException,
  Logger,
} from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository, MoreThan } from 'typeorm';
import { JwtService } from '@nestjs/jwt';
import { ConfigService } from '@nestjs/config';
import * as bcrypt from 'bcrypt';
import * as crypto from 'crypto';
import { User } from './entities/user.entity';
import { RefreshToken } from './entities/refresh-token.entity';
import { AuditLogService } from './audit-log.service';

export interface TokenPair {
  access_token: string;
  refresh_token: string;
}

@Injectable()
export class AuthService {
  private readonly logger = new Logger(AuthService.name);

  constructor(
    @InjectRepository(User)
    private readonly userRepo: Repository<User>,
    @InjectRepository(RefreshToken)
    private readonly refreshTokenRepo: Repository<RefreshToken>,
    private readonly jwtService: JwtService,
    private readonly config: ConfigService,
    private readonly auditLogService: AuditLogService,
  ) {}

  async login(email: string, password: string, ipAddress?: string): Promise<TokenPair> {
    const user = await this.userRepo.findOne({ where: { email } });
    if (!user) {
      await this.auditLogService.log('login_failed', null, ipAddress ?? null, { email, reason: 'user_not_found' });
      throw new UnauthorizedException('Invalid credentials');
    }

    const valid = await bcrypt.compare(password, user.passwordHash);
    if (!valid) {
      await this.auditLogService.log('login_failed', user.id, ipAddress ?? null, { email, reason: 'invalid_password' });
      throw new UnauthorizedException('Invalid credentials');
    }

    // Disabled users keep their data but can't authenticate.
    if (user.isActive === false) {
      await this.auditLogService.log('login_failed', user.id, ipAddress ?? null, { email, reason: 'account_disabled' });
      throw new UnauthorizedException('Account is disabled');
    }

    const tokens = await this.issueTokens(user);
    await this.auditLogService.log('login', user.id, ipAddress ?? null);
    return tokens;
  }

  async refresh(rawRefreshToken: string, ipAddress?: string): Promise<TokenPair> {
    const tokenHash = this.hashToken(rawRefreshToken);

    const stored = await this.refreshTokenRepo.findOne({
      where: {
        tokenHash,
        expiresAt: MoreThan(new Date()),
      },
      relations: ['user'],
    });

    if (!stored) {
      await this.auditLogService.log('refresh_failed', null, ipAddress ?? null, { reason: 'invalid_or_expired_token' });
      throw new UnauthorizedException('Invalid or expired refresh token');
    }

    // Rotate: delete old token, issue new pair
    await this.refreshTokenRepo.remove(stored);

    const tokens = await this.issueTokens(stored.user);
    await this.auditLogService.log('refresh', stored.user.id, ipAddress ?? null);
    return tokens;
  }

  async logout(rawRefreshToken: string, userId?: string, ipAddress?: string): Promise<void> {
    const tokenHash = this.hashToken(rawRefreshToken);
    await this.refreshTokenRepo.delete({ tokenHash });
    await this.auditLogService.log('logout', userId ?? null, ipAddress ?? null);
  }

  private async issueTokens(user: User): Promise<TokenPair> {
    const payload = { sub: user.id, email: user.email, role: user.role };

    const accessToken = this.jwtService.sign(payload, {
      secret: this.config.get<string>('JWT_SECRET'),
      expiresIn: this.config.get<string>('JWT_ACCESS_EXPIRY') || '15m',
    });

    const rawRefreshToken = crypto.randomBytes(40).toString('hex');
    const tokenHash = this.hashToken(rawRefreshToken);

    const refreshExpiry = this.config.get<string>('JWT_REFRESH_EXPIRY') || '7d';
    const expiresAt = new Date(
      Date.now() + this.parseDuration(refreshExpiry),
    );

    const refreshToken = this.refreshTokenRepo.create({
      userId: user.id,
      tokenHash,
      expiresAt,
    });
    await this.refreshTokenRepo.save(refreshToken);

    return { access_token: accessToken, refresh_token: rawRefreshToken };
  }

  private hashToken(token: string): string {
    return crypto.createHash('sha256').update(token).digest('hex');
  }

  private parseDuration(duration: string): number {
    const match = duration.match(/^(\d+)([smhd])$/);
    if (!match) return 7 * 24 * 60 * 60 * 1000; // default 7 days
    const value = parseInt(match[1], 10);
    const unit = match[2];
    const multipliers: Record<string, number> = {
      s: 1000,
      m: 60 * 1000,
      h: 60 * 60 * 1000,
      d: 24 * 60 * 60 * 1000,
    };
    return value * (multipliers[unit] || 1);
  }
}
