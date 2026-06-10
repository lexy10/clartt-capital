import { IsBoolean, IsOptional, IsIn } from 'class-validator';

export class KillSwitchDto {
  @IsBoolean()
  active: boolean;

  @IsOptional()
  @IsIn(['soft', 'hard'])
  mode?: 'soft' | 'hard' = 'soft';
}

