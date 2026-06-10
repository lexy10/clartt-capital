import { IsArray, IsUUID } from 'class-validator';

export class SetAccountStrategiesDto {
  @IsArray()
  @IsUUID('4', { each: true })
  strategyIds: string[];
}
