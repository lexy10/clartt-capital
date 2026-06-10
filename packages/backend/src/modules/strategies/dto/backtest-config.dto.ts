import { IsUUID, IsObject, IsOptional, IsString } from 'class-validator';

export class BacktestConfigDto {
  @IsUUID()
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
