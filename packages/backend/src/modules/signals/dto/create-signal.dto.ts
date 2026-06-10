import {
  IsString,
  IsIn,
  IsNumber,
  Min,
  Max,
  IsOptional,
  IsUUID,
  IsObject,
} from 'class-validator';

export class CreateSignalDto {
  @IsString()
  instrument: string;

  @IsIn(['BUY', 'SELL'])
  direction: string;

  @IsNumber()
  entryPrice: number;

  @IsNumber()
  stopLoss: number;

  @IsNumber()
  takeProfit: number;

  @IsNumber()
  positionSize: number;

  @IsNumber()
  @Min(0)
  @Max(1)
  confidenceScore: number;

  @IsString()
  timeframe: string;

  @IsOptional()
  @IsString()
  orderBlockId?: string;

  @IsOptional()
  @IsUUID()
  strategyId?: string;

  @IsIn(['live', 'forward_test', 'backtest'])
  mode: string;

  @IsOptional()
  @IsObject()
  metadata?: Record<string, unknown>;
}
