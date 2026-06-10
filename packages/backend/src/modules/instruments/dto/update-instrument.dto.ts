import { PartialType } from '@nestjs/mapped-types';
import { IsBoolean, IsOptional } from 'class-validator';
import { CreateInstrumentDto } from './create-instrument.dto';

export class UpdateInstrumentDto extends PartialType(CreateInstrumentDto) {
  @IsBoolean()
  @IsOptional()
  isActive?: boolean;
}
