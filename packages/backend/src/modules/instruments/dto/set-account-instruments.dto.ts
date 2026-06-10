import { Type } from 'class-transformer';
import {
  IsArray,
  IsOptional,
  IsString,
  IsUUID,
  ValidateNested,
} from 'class-validator';

export class AccountInstrumentItemDto {
  @IsUUID()
  instrumentId: string;

  @IsOptional()
  @IsString()
  brokerSymbol?: string;
}

export class SetAccountInstrumentsDto {
  @IsArray()
  @ValidateNested({ each: true })
  @Type(() => AccountInstrumentItemDto)
  instruments: AccountInstrumentItemDto[];
}
