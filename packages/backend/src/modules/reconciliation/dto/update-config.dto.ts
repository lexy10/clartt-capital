import {
  IsOptional,
  IsNumber,
  IsBoolean,
  IsInt,
  Min,
} from 'class-validator';

export class UpdateConfigDto {
  @IsOptional()
  @IsInt()
  @Min(30)
  reconciliationIntervalSeconds?: number;

  @IsOptional()
  @IsNumber()
  @Min(0.01)
  balanceDriftThreshold?: number;

  @IsOptional()
  @IsNumber()
  @Min(0.01)
  equityDriftThreshold?: number;

  @IsOptional()
  @IsNumber()
  @Min(0.0001)
  positionSizeDriftThreshold?: number;

  @IsOptional()
  @IsBoolean()
  autoCorrectPhantomPositions?: boolean;

  @IsOptional()
  @IsBoolean()
  autoCorrectMissingPositions?: boolean;

  @IsOptional()
  @IsBoolean()
  autoCorrectBalanceDrift?: boolean;

  @IsOptional()
  @IsInt()
  @Min(1)
  escalationCycleCount?: number;
}
