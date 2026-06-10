import {
  Controller,
  Get,
  Post,
  Body,
  Param,
  Query,
  UseGuards,
  ParseUUIDPipe,
} from '@nestjs/common';
import { SignalsService } from './signals.service';
import { JwtAuthGuard } from '../../common/guards/jwt-auth.guard';
import { CreateSignalDto } from './dto/create-signal.dto';

@Controller('signals')
export class SignalsController {
  constructor(private readonly signalsService: SignalsService) {}

  @Post()
  create(@Body() dto: CreateSignalDto) {
    return this.signalsService.create(dto);
  }

  @Get()
  @UseGuards(JwtAuthGuard)
  findAll(
    @Query('limit') limit?: string,
    @Query('offset') offset?: string,
  ) {
    return this.signalsService.findAll({
      limit: limit ? parseInt(limit, 10) : undefined,
      offset: offset ? parseInt(offset, 10) : undefined,
    });
  }

  @Get(':id')
  @UseGuards(JwtAuthGuard)
  findById(@Param('id', ParseUUIDPipe) id: string) {
    return this.signalsService.findById(id);
  }
}
