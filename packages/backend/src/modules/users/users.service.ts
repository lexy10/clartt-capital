import { Injectable, NotFoundException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { User } from '../auth/entities/user.entity';
import { UpdateUserDto } from './dto/update-user.dto';

@Injectable()
export class UsersService {
  constructor(
    @InjectRepository(User)
    private readonly usersRepository: Repository<User>,
  ) {}

  async getProfile(userId: string): Promise<Omit<User, 'passwordHash' | 'refreshTokens'>> {
    const user = await this.usersRepository.findOne({ where: { id: userId } });
    if (!user) {
      throw new NotFoundException('User not found');
    }
    const { passwordHash, refreshTokens, ...profile } = user;
    return profile;
  }

  async updateProfile(
    userId: string,
    dto: UpdateUserDto,
  ): Promise<Omit<User, 'passwordHash' | 'refreshTokens'>> {
    const user = await this.usersRepository.findOne({ where: { id: userId } });
    if (!user) {
      throw new NotFoundException('User not found');
    }

    if (dto.email !== undefined) {
      user.email = dto.email;
    }

    const saved = await this.usersRepository.save(user);
    const { passwordHash, refreshTokens, ...profile } = saved;
    return profile;
  }

  async getById(id: string): Promise<Omit<User, 'passwordHash' | 'refreshTokens'>> {
    const user = await this.usersRepository.findOne({ where: { id } });
    if (!user) {
      throw new NotFoundException('User not found');
    }
    const { passwordHash, refreshTokens, ...profile } = user;
    return profile;
  }

  /** List all users — used by the admin user-switcher dropdown.
   *  Order by email so the dropdown is stable. */
  async listAll(): Promise<Array<Pick<User, 'id' | 'email' | 'role' | 'createdAt'>>> {
    const users = await this.usersRepository.find({
      order: { email: 'ASC' },
    });
    return users.map(({ id, email, role, createdAt }) => ({ id, email, role, createdAt }));
  }
}
