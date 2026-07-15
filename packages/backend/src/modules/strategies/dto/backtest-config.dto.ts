import { IsObject, IsOptional, IsString, Matches } from 'class-validator';

// UUID by shape, not version — some seeded strategies have hand-crafted IDs
// whose version nibble isn't 4 (see set-account-strategies.dto), so @IsUUID('4')
// / @IsUUID() would 400 a backtest on e.g. V25 Structure Scalper.
const UUID_SHAPE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export class BacktestConfigDto {
  @Matches(UUID_SHAPE, { message: 'strategyId must be a UUID' })
  strategyId: string;

  @IsString()
  @IsOptional()
  instrument?: string;

  @IsString()
  @IsOptional()
  timeframe?: string;

  @IsObject()
  @IsOptional()
  parameters?: Record<string, unknown>;

  @IsOptional()
  startDate?: string;

  @IsOptional()
  endDate?: string;
}
