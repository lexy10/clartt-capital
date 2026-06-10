import {
  CallHandler,
  ExecutionContext,
  Injectable,
  Logger,
  NestInterceptor,
} from '@nestjs/common';
import { Observable } from 'rxjs';

/**
 * EffectiveUserInterceptor — implements admin "view as" impersonation.
 *
 * When an admin sets `?asUserId=<uuid>` on any request, this interceptor
 * rewrites `req.user.id` to that user's id BEFORE the controller runs.
 * Every `req.user.id`-based query in the app (37+ call sites) then
 * automatically scopes to the impersonated user — no controller changes
 * needed.
 *
 * Rules:
 *   - Caller must be authenticated (req.user must already be populated by
 *     the JWT guard).
 *   - Caller must have role === 'admin'. Non-admins who try to inject
 *     asUserId are silently ignored — the param has no effect.
 *   - The original user id is preserved under req.actualUserId so audit
 *     code (logging, billing) can still tell who actually issued the call.
 *
 * The frontend's ApiClient attaches `asUserId` automatically via the
 * request interceptor whenever an admin has picked a non-self user in
 * the top-bar UserSwitcher.
 */
@Injectable()
export class EffectiveUserInterceptor implements NestInterceptor {
  private readonly logger = new Logger(EffectiveUserInterceptor.name);

  intercept(context: ExecutionContext, next: CallHandler): Observable<unknown> {
    const req = context.switchToHttp().getRequest<{
      user?: { id: string; email: string; role: string };
      actualUserId?: string;
      query?: Record<string, string | string[] | undefined>;
      url?: string;
    }>();

    const user = req?.user;
    const rawParam = req?.query?.asUserId;
    const asUserId = Array.isArray(rawParam) ? rawParam[0] : rawParam;

    if (user && typeof asUserId === 'string' && asUserId.length > 0) {
      if (user.role === 'admin' && asUserId !== user.id) {
        // Preserve the real id for audit, then rewrite to the impersonated id.
        req.actualUserId = user.id;
        // We mutate the existing object so any downstream code that holds
        // a reference still sees the effective id.
        user.id = asUserId;
        this.logger.debug?.(
          `Admin ${req.actualUserId} viewing as ${asUserId} (${req.url ?? 'unknown URL'})`,
        );
      }
      // Non-admin attempted impersonation — silently ignored. Their token
      // already binds them to their own data; no privilege escalation.
    }

    return next.handle();
  }
}
