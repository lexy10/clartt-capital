import { IsString, IsArray, IsOptional, IsNotEmpty } from 'class-validator';

export class UpdateWatchlistDto {
  @IsOptional()
  @IsString()
  @IsNotEmpty()
  name?: string;

  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  instruments?: string[];
}
