import {
  Controller,
  Get,
  Post,
  Put,
  Delete,
  Body,
  Param,
  UseGuards,
  Request,
} from '@nestjs/common';
import { WatchlistsService } from './watchlists.service';
import { CreateWatchlistDto } from './dto/create-watchlist.dto';
import { UpdateWatchlistDto } from './dto/update-watchlist.dto';
import { JwtAuthGuard } from '../../common/guards/jwt-auth.guard';

@Controller('watchlists')
@UseGuards(JwtAuthGuard)
export class WatchlistsController {
  constructor(private readonly watchlistsService: WatchlistsService) {}

  @Get()
  findAll(@Request() req: any) {
    return this.watchlistsService.findAllByUser(req.user.id);
  }

  @Post()
  create(@Request() req: any, @Body() dto: CreateWatchlistDto) {
    return this.watchlistsService.create(req.user.id, dto);
  }

  @Put(':id')
  update(
    @Request() req: any,
    @Param('id') id: string,
    @Body() dto: UpdateWatchlistDto,
  ) {
    return this.watchlistsService.update(req.user.id, id, dto);
  }

  @Delete(':id')
  remove(@Request() req: any, @Param('id') id: string) {
    return this.watchlistsService.remove(req.user.id, id);
  }
}
