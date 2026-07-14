import { IsString, IsNotEmpty, IsOptional } from 'class-validator';

/** Update a Deriv-direct account's API token (and optionally its login ID) —
 *  used to fix an expired/invalid/revoked token without removing and
 *  re-adding the account. */
export class UpdateDerivTokenDto {
  @IsString()
  @IsNotEmpty()
  derivApiToken!: string;

  @IsString()
  @IsOptional()
  derivLoginId?: string;
}
