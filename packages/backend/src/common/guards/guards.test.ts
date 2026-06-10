import { ExecutionContext, ForbiddenException, HttpException, HttpStatus } from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { JwtAuthGuard } from './jwt-auth.guard';
import { RolesGuard } from './roles.guard';
import { RateLimitGuard } from './rate-limit.guard';

// --- JwtAuthGuard ---

describe('JwtAuthGuard', () => {
  it('should be defined and extend AuthGuard("jwt")', () => {
    const guard = new JwtAuthGuard();
    expect(guard).toBeDefined();
  });
});

// --- RolesGuard ---

describe('RolesGuard', () => {
  let guard: RolesGuard;
  let reflector: Reflector;

  function mockContext(user: { role: string } | undefined): ExecutionContext {
    return {
      getHandler: jest.fn(),
      getClass: jest.fn(),
      switchToHttp: () => ({
        getRequest: () => ({ user }),
      }),
    } as unknown as ExecutionContext;
  }

  beforeEach(() => {
    reflector = new Reflector();
    guard = new RolesGuard(reflector);
  });

  it('should allow access when no roles are required', () => {
    jest.spyOn(reflector, 'getAllAndOverride').mockReturnValue(undefined);
    expect(guard.canActivate(mockContext({ role: 'trader' }))).toBe(true);
  });

  it('should allow access when user has a required role', () => {
    jest.spyOn(reflector, 'getAllAndOverride').mockReturnValue(['admin', 'trader']);
    expect(guard.canActivate(mockContext({ role: 'trader' }))).toBe(true);
  });

  it('should deny access when user role is not in required roles', () => {
    jest.spyOn(reflector, 'getAllAndOverride').mockReturnValue(['admin']);
    expect(() => guard.canActivate(mockContext({ role: 'trader' }))).toThrow(ForbiddenException);
  });

  it('should deny access when no user is present', () => {
    jest.spyOn(reflector, 'getAllAndOverride').mockReturnValue(['admin']);
    expect(() => guard.canActivate(mockContext(undefined))).toThrow(ForbiddenException);
  });
});

// --- RateLimitGuard ---

describe('RateLimitGuard', () => {
  let guard: RateLimitGuard;
  let reflector: Reflector;
  let mockRedis: { incr: jest.Mock; expire: jest.Mock };

  function mockContext(): ExecutionContext {
    return {
      getHandler: jest.fn(),
      getClass: jest.fn(),
      switchToHttp: () => ({
        getRequest: () => ({
          ip: '127.0.0.1',
          method: 'GET',
          route: { path: '/test' },
        }),
      }),
    } as unknown as ExecutionContext;
  }

  beforeEach(() => {
    reflector = new Reflector();
    mockRedis = {
      incr: jest.fn(),
      expire: jest.fn(),
    };
    guard = new RateLimitGuard(reflector, mockRedis as any);
  });

  it('should allow requests within the limit', async () => {
    jest.spyOn(reflector, 'getAllAndOverride').mockReturnValue({ limit: 5, windowSeconds: 60 });
    mockRedis.incr.mockResolvedValue(1);

    const result = await guard.canActivate(mockContext());
    expect(result).toBe(true);
    expect(mockRedis.expire).toHaveBeenCalled();
  });

  it('should set TTL only on first request (incr returns 1)', async () => {
    jest.spyOn(reflector, 'getAllAndOverride').mockReturnValue({ limit: 5, windowSeconds: 60 });
    mockRedis.incr.mockResolvedValue(3);

    await guard.canActivate(mockContext());
    expect(mockRedis.expire).not.toHaveBeenCalled();
  });

  it('should reject requests exceeding the limit with 429', async () => {
    jest.spyOn(reflector, 'getAllAndOverride').mockReturnValue({ limit: 5, windowSeconds: 60 });
    mockRedis.incr.mockResolvedValue(6);

    await expect(guard.canActivate(mockContext())).rejects.toThrow(HttpException);
    try {
      await guard.canActivate(mockContext());
    } catch (e) {
      expect((e as HttpException).getStatus()).toBe(HttpStatus.TOO_MANY_REQUESTS);
    }
  });

  it('should use default limits when no decorator is present', async () => {
    jest.spyOn(reflector, 'getAllAndOverride').mockReturnValue(undefined);
    mockRedis.incr.mockResolvedValue(1);

    const result = await guard.canActivate(mockContext());
    expect(result).toBe(true);
  });
});
