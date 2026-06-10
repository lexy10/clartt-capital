import { IsString, IsNotEmpty } from 'class-validator';

export class UpdateLabelDto {
  @IsString()
  @IsNotEmpty()
  label: string;
}
