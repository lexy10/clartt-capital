import {
  IsOptional,
  IsString,
  MaxLength,
  IsObject,
  IsBoolean,
} from 'class-validator';

export class UpdateStrategyDto {
  @IsOptional()
  @IsString()
  @MaxLength(255)
  name?: string;

  @IsOptional()
  @IsString()
  @MaxLength(100)
  algorithm?: string;

  @IsOptional()
  @IsObject()
  config?: Record<string, unknown>;

  @IsOptional()
  @IsBoolean()
  enabled?: boolean;
}
