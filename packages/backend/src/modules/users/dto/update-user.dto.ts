import { IsEmail, IsIn, IsOptional, IsObject, ValidateNested } from 'class-validator';
import { Type } from 'class-transformer';

export class ThemeDto {
  @IsOptional()
  @IsIn(['light', 'dark', 'system'])
  mode?: string;

  @IsOptional()
  @IsIn(['indigo', 'emerald', 'sky', 'amber', 'rose'])
  accent?: string;
}

export class UpdateUserDto {
  @IsOptional()
  @IsEmail()
  email?: string;

  @IsOptional()
  @IsObject()
  @ValidateNested()
  @Type(() => ThemeDto)
  theme?: ThemeDto;
}
