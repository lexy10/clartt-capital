import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import { JwtService } from '@nestjs/jwt';
import { ConfigService } from '@nestjs/config';
import { UnauthorizedException } from '@nestjs/common';
import * as bcrypt from 'bcrypt';
import * as crypto from 'crypto';
import { AuthService } from './auth.service';
import { User } from './entities/user.entity';
import { RefreshToken } from './entities/refresh-token.entity';
import { AuditLogService } from './audit-log.service';

describe('AuthService', () => {
  let service: AuthService;
  let userRepo: any;
  let refreshTokenRepo: any;
  let jwtService: JwtService;
  let auditLogService: AuditLogService;

  const mockUser: Partial<User> = {
    id: 'user-uuid-1',
    email: 'trader@example.com',
    passwordHash: '',
    role: 'trader',
  };

  beforeAll(async () => {
    mockUser.passwordHash = await bcrypt.hash('password123', 10);
  });

  beforeEach(async () => {
    userRepo = {
      findOne: jest.fn(),
    };
    refreshTokenRepo = {
      findOne: jest.fn(),
      create: jest.fn((dto) => dto),
      save: jest.fn((entity) => Promise.resolve(entity)),
      remove: jest.fn(),
      delete: jest.fn(),
    };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        AuthService,
        { provide: getRepositoryToken(User), useValue: userRepo },
        { provide: getRepositoryToken(RefreshToken), useValue: refreshTokenRepo },
        {
          provide: JwtService,
          useValue: { sign: jest.fn().mockReturnValue('mock-access-token') },
        },
        {
          provide: ConfigService,
          useValue: {
            get: jest.fn((key: string) => {
              const map: Record<string, string> = {
                JWT_SECRET: 'test-secret',
                JWT_ACCESS_EXPIRY: '15m',
                JWT_REFRESH_EXPIRY: '7d',
              };
              return map[key];
            }),
          },
        },
        {
          provide: AuditLogService,
          useValue: { log: jest.fn().mockResolvedValue(undefined) },
        },
      ],
    }).compile();

    service = module.get<AuthService>(AuthService);
    jwtService = module.get<JwtService>(JwtService);
    auditLogService = module.get<AuditLogService>(AuditLogService);
  });

  describe('login', () => {
    it('should return tokens for valid credentials', async () => {
      userRepo.findOne.mockResolvedValue(mockUser);

      const result = await service.login('trader@example.com', 'password123');

      expect(result.access_token).toBe('mock-access-token');
      expect(result.refresh_token).toBeDefined();
      expect(typeof result.refresh_token).toBe('string');
      expect(result.refresh_token.length).toBe(80); // 40 bytes hex
      expect(refreshTokenRepo.save).toHaveBeenCalled();
    });

    it('should log successful login', async () => {
      userRepo.findOne.mockResolvedValue(mockUser);

      await service.login('trader@example.com', 'password123', '127.0.0.1');

      expect(auditLogService.log).toHaveBeenCalledWith('login', 'user-uuid-1', '127.0.0.1');
    });

    it('should throw for non-existent user and log failure', async () => {
      userRepo.findOne.mockResolvedValue(null);

      await expect(
        service.login('nobody@example.com', 'password123', '127.0.0.1'),
      ).rejects.toThrow(UnauthorizedException);

      expect(auditLogService.log).toHaveBeenCalledWith(
        'login_failed', null, '127.0.0.1',
        expect.objectContaining({ email: 'nobody@example.com', reason: 'user_not_found' }),
      );
    });

    it('should throw for wrong password and log failure', async () => {
      userRepo.findOne.mockResolvedValue(mockUser);

      await expect(
        service.login('trader@example.com', 'wrongpassword', '127.0.0.1'),
      ).rejects.toThrow(UnauthorizedException);

      expect(auditLogService.log).toHaveBeenCalledWith(
        'login_failed', 'user-uuid-1', '127.0.0.1',
        expect.objectContaining({ reason: 'invalid_password' }),
      );
    });
  });

  describe('refresh', () => {
    it('should rotate tokens on valid refresh', async () => {
      const rawToken = crypto.randomBytes(40).toString('hex');
      const tokenHash = crypto.createHash('sha256').update(rawToken).digest('hex');

      const storedToken = {
        id: 'token-uuid',
        tokenHash,
        expiresAt: new Date(Date.now() + 86400000),
        user: mockUser,
      };
      refreshTokenRepo.findOne.mockResolvedValue(storedToken);

      const result = await service.refresh(rawToken);

      expect(refreshTokenRepo.remove).toHaveBeenCalledWith(storedToken);
      expect(result.access_token).toBe('mock-access-token');
      expect(result.refresh_token).toBeDefined();
      expect(refreshTokenRepo.save).toHaveBeenCalled();
    });

    it('should log successful refresh', async () => {
      const rawToken = crypto.randomBytes(40).toString('hex');
      const tokenHash = crypto.createHash('sha256').update(rawToken).digest('hex');

      const storedToken = {
        id: 'token-uuid',
        tokenHash,
        expiresAt: new Date(Date.now() + 86400000),
        user: mockUser,
      };
      refreshTokenRepo.findOne.mockResolvedValue(storedToken);

      await service.refresh(rawToken, '10.0.0.1');

      expect(auditLogService.log).toHaveBeenCalledWith('refresh', 'user-uuid-1', '10.0.0.1');
    });

    it('should throw for invalid refresh token and log failure', async () => {
      refreshTokenRepo.findOne.mockResolvedValue(null);

      await expect(service.refresh('invalid-token', '10.0.0.1')).rejects.toThrow(
        UnauthorizedException,
      );

      expect(auditLogService.log).toHaveBeenCalledWith(
        'refresh_failed', null, '10.0.0.1',
        expect.objectContaining({ reason: 'invalid_or_expired_token' }),
      );
    });
  });

  describe('logout', () => {
    it('should delete the refresh token and log', async () => {
      const rawToken = crypto.randomBytes(40).toString('hex');
      const tokenHash = crypto.createHash('sha256').update(rawToken).digest('hex');

      await service.logout(rawToken, 'user-uuid-1', '192.168.1.1');

      expect(refreshTokenRepo.delete).toHaveBeenCalledWith({ tokenHash });
      expect(auditLogService.log).toHaveBeenCalledWith('logout', 'user-uuid-1', '192.168.1.1');
    });
  });
});
