import { IsString, IsEmail, IsNotEmpty, MinLength, IsIn, IsBoolean } from 'class-validator';
import { VALID_ROLES } from '../users.service';

export class CreateUserDto {
  @IsEmail()
  email!: string;

  @IsString()
  @MinLength(8, { message: 'Password must be at least 8 characters' })
  password!: string;

  @IsString()
  @IsIn(VALID_ROLES as unknown as string[])
  role!: string;
}

export class UpdateRoleDto {
  @IsString()
  @IsIn(VALID_ROLES as unknown as string[])
  role!: string;
}

export class SetActiveDto {
  @IsBoolean()
  isActive!: boolean;
}

export class ResetPasswordDto {
  @IsString()
  @IsNotEmpty()
  @MinLength(8, { message: 'Password must be at least 8 characters' })
  password!: string;
}

export class ChangePasswordDto {
  @IsString()
  @IsNotEmpty()
  currentPassword!: string;

  @IsString()
  @MinLength(8, { message: 'New password must be at least 8 characters' })
  newPassword!: string;
}
