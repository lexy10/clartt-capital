import { IsString, IsNotEmpty, IsIn, IsOptional } from 'class-validator';

/**
 * Two flows depending on broker:
 *
 * 1. MetaAPI (MT5/MT4 — forex, commodities, indices, or Deriv via MT5):
 *    Provide { login, password, serverName, platform }. Backend calls the
 *    execution engine /accounts/provision endpoint which talks to MetaAPI.
 *
 * 2. Deriv direct (synthetics — R_*, BOOM_*, CRASH_*):
 *    Set brokerProvider="deriv". Only a derivApiToken + derivLoginId are
 *    required; no MT5 credentials. The execution engine connects directly
 *    to Deriv's WebSocket API.
 */
export class CreateAccountDto {
  // ── MetaAPI / MT5 flow ────────────────────────────────────────────────
  @IsString()
  @IsOptional()
  login?: string;

  @IsString()
  @IsOptional()
  password?: string;

  @IsString()
  @IsOptional()
  serverName?: string;

  @IsString()
  @IsOptional()
  @IsIn(['mt5', 'mt4'])
  platform?: string;

  // ── Deriv direct flow ─────────────────────────────────────────────────

  /** Deriv API token (free from app.deriv.com/account/api-token).
   *  Only used when brokerProvider="deriv". */
  @IsString()
  @IsOptional()
  derivApiToken?: string;

  /** Deriv loginid (e.g. "CR1234567"). Identifies which Deriv account the
   *  token belongs to. Only used when brokerProvider="deriv". */
  @IsString()
  @IsOptional()
  derivLoginId?: string;

  // ── Common ────────────────────────────────────────────────────────────

  /** Broker provider. Defaults to "metaapi" for backward compatibility.
   *  Use "deriv" for Deriv-direct accounts (synthetics). */
  @IsString()
  @IsOptional()
  @IsIn(['metaapi', 'deriv'])
  brokerProvider?: 'metaapi' | 'deriv';

  /** Account funding type. */
  @IsString()
  @IsOptional()
  @IsIn(['personal', 'prop', 'demo'])
  accountKind?: 'personal' | 'prop' | 'demo';

  @IsString()
  @IsOptional()
  label?: string;
}
