import {
  IsOptional,
  IsString,
  IsEnum,
  IsInt,
  IsBoolean,
  IsIn,
  Min,
  Max,
} from 'class-validator';
import { Transform, Type } from 'class-transformer';
import { TradingEventType } from '../enums/trading-event-type.enum';

export class EventQueryDto {
  @IsOptional()
  @IsString()
  account_id?: string;

  @IsOptional()
  @IsString()
  instrument?: string;

  @IsOptional()
  @IsEnum(TradingEventType)
  event_type?: TradingEventType;

  @IsOptional()
  @IsString()
  correlation_id?: string;

  @IsOptional()
  @IsString()
  aggregate_id?: string;

  @IsOptional()
  @IsString()
  start_time?: string;

  @IsOptional()
  @IsString()
  end_time?: string;

  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(1)
  page?: number = 1;

  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(200)
  page_size?: number = 50;

  @IsOptional()
  @IsIn(['asc', 'desc'])
  sort?: 'asc' | 'desc' = 'desc';

  @IsOptional()
  @Transform(({ value }) => value === 'true' || value === true)
  @IsBoolean()
  include_archived?: boolean = false;
}
