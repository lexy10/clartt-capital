import {
  IsEnum,
  IsString,
  IsInt,
  IsOptional,
  IsObject,
} from 'class-validator';
import { TradingEventType } from '../enums/trading-event-type.enum';

export class TradingEventDto {
  @IsEnum(TradingEventType)
  event_type: TradingEventType;

  @IsString()
  aggregate_id: string;

  @IsInt()
  sequence_number: number;

  @IsOptional()
  @IsString()
  correlation_id?: string;

  @IsObject()
  payload: Record<string, unknown>;

  @IsOptional()
  @IsObject()
  context_snapshot?: Record<string, unknown>;

  @IsString()
  source_service: string;

  @IsOptional()
  @IsInt()
  schema_version?: number = 1;
}
