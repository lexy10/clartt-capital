import {
  IsString,
  MaxLength,
  IsObject,
  IsOptional,
} from 'class-validator';

export class CreateStrategyDto {
  @IsString()
  @MaxLength(255)
  name: string;

  @IsOptional()
  @IsString()
  @MaxLength(100)
  algorithm?: string;

  @IsObject()
  config: Record<string, unknown>;
}
