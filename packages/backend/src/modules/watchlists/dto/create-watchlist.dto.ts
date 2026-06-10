import { IsString, IsArray, IsNotEmpty, ArrayMinSize } from 'class-validator';

export class CreateWatchlistDto {
  @IsString()
  @IsNotEmpty()
  name: string;

  @IsArray()
  @IsString({ each: true })
  @ArrayMinSize(0)
  instruments: string[];
}
