import { Injectable, NotFoundException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Watchlist } from './entities/watchlist.entity';
import { CreateWatchlistDto } from './dto/create-watchlist.dto';
import { UpdateWatchlistDto } from './dto/update-watchlist.dto';

@Injectable()
export class WatchlistsService {
  constructor(
    @InjectRepository(Watchlist)
    private readonly watchlistsRepository: Repository<Watchlist>,
  ) {}

  async findAllByUser(userId: string): Promise<Watchlist[]> {
    return this.watchlistsRepository.find({ where: { userId } });
  }

  async create(userId: string, dto: CreateWatchlistDto): Promise<Watchlist> {
    const watchlist = this.watchlistsRepository.create({
      userId,
      name: dto.name,
      instruments: dto.instruments,
    });
    return this.watchlistsRepository.save(watchlist);
  }

  async update(userId: string, id: string, dto: UpdateWatchlistDto): Promise<Watchlist> {
    const watchlist = await this.watchlistsRepository.findOne({
      where: { id, userId },
    });
    if (!watchlist) {
      throw new NotFoundException('Watchlist not found');
    }

    if (dto.name !== undefined) {
      watchlist.name = dto.name;
    }
    if (dto.instruments !== undefined) {
      watchlist.instruments = dto.instruments;
    }

    return this.watchlistsRepository.save(watchlist);
  }

  async remove(userId: string, id: string): Promise<void> {
    const watchlist = await this.watchlistsRepository.findOne({
      where: { id, userId },
    });
    if (!watchlist) {
      throw new NotFoundException('Watchlist not found');
    }
    await this.watchlistsRepository.remove(watchlist);
  }
}
