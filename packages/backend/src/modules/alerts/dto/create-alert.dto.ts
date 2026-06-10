import { IsString, IsNotEmpty, IsObject } from 'class-validator';

export class CreateAlertDto {
  @IsString()
  @IsNotEmpty()
  instrument: string;

  @IsString()
  @IsNotEmpty()
  conditionType: string;

  @IsObject()
  conditionValue: Record<string, unknown>;
}
