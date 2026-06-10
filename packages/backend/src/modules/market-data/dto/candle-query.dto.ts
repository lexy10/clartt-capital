import { IsString, IsOptional, IsIn, IsNumberString } from 'class-validator';

export class CandleQueryDto {
  @IsString()
  instrument: string;

  @IsIn(['1m', '5m', '15m', '30m', '1h', '4h', '1d'])
  timeframe: '1m' | '5m' | '15m' | '30m' | '1h' | '4h' | '1d';

  @IsOptional()
  @IsNumberString()
  count?: string;

  @IsOptional()
  @IsString()
  startDate?: string;

  @IsOptional()
  @IsString()
  endDate?: string;
}
