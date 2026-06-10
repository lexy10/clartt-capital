import { IsString, IsNotEmpty, IsIn, IsOptional, IsNumber, IsInt, Min } from 'class-validator';

export class CreateInstrumentDto {
  @IsString()
  @IsNotEmpty()
  symbol: string;

  @IsString()
  @IsNotEmpty()
  displayName: string;

  @IsString()
  @IsIn(['index', 'commodity', 'synthetic'])
  type: 'index' | 'commodity' | 'synthetic';

  @IsString()
  @IsOptional()
  derivSymbol?: string;

  @IsNumber()
  @IsOptional()
  @Min(0.000001)
  contractSize?: number;

  @IsNumber()
  @IsOptional()
  @Min(0.00000001)
  pipSize?: number;

  @IsNumber()
  @IsOptional()
  @Min(0.000001)
  pipValue?: number;

  @IsNumber()
  @IsOptional()
  @Min(0.0001)
  minLot?: number;

  @IsNumber()
  @IsOptional()
  @Min(0.0001)
  lotStep?: number;

  @IsInt()
  @IsOptional()
  @Min(1)
  leverage?: number;
}
