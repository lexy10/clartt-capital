import { Injectable, CanActivate, ExecutionContext, ForbiddenException } from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { ROLES_KEY } from '../decorators/roles.decorator';

@Injectable()
export class RolesGuard implements CanActivate {
  constructor(private readonly reflector: Reflector) {}

  canActivate(context: ExecutionContext): boolean {
    const requiredRoles = this.reflector.getAllAndOverride<string[]>(ROLES_KEY, [
      context.getHandler(),
      context.getClass(),
    ]);

    // No @Roles() decorator — allow access
    if (!requiredRoles || requiredRoles.length === 0) {
      return true;
    }

    const request = context.switchToHttp().getRequest();
    const user = request.user;

    if (!user || !user.role) {
      throw new ForbiddenException('Access denied');
    }

    // Role hierarchy: a higher rank satisfies any lower-or-equal requirement.
    // So @Roles('admin') is satisfied by both 'admin' and 'superadmin', while
    // @Roles('superadmin') is satisfied only by 'superadmin'.
    const rank: Record<string, number> = { trader: 1, admin: 2, superadmin: 3 };
    const userRank = rank[user.role] ?? 0;
    const requiredRank = Math.min(...requiredRoles.map((r) => rank[r] ?? Infinity));

    if (userRank < requiredRank) {
      throw new ForbiddenException('Insufficient role');
    }

    return true;
  }
}
