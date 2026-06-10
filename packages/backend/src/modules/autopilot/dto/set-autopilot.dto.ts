import { IsBoolean } from 'class-validator';

export class SetAutopilotDto {
  @IsBoolean()
  enabled: boolean;
}
