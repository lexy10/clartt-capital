import { Injectable, NotFoundException, ConflictException, BadRequestException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import * as bcrypt from 'bcrypt';
import { User } from '../auth/entities/user.entity';
import { UpdateUserDto } from './dto/update-user.dto';

/** Roles the platform recognises. Keep in sync with RolesGuard usage. */
export const VALID_ROLES = ['admin', 'trader'] as const;
export type UserRole = (typeof VALID_ROLES)[number];
const BCRYPT_ROUNDS = 10;

export type AdminUserView = Pick<User, 'id' | 'email' | 'role' | 'isActive' | 'createdAt'>;

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

  /** List all users — used by the admin user-switcher dropdown and the
   *  user-management page. Order by email so the list is stable. */
  async listAll(): Promise<AdminUserView[]> {
    const users = await this.usersRepository.find({ order: { email: 'ASC' } });
    return users.map(({ id, email, role, isActive, createdAt }) => ({
      id, email, role, isActive, createdAt,
    }));
  }

  // ── Admin user management ──────────────────────────────────────────────

  private toView(user: User): AdminUserView {
    return {
      id: user.id, email: user.email, role: user.role,
      isActive: user.isActive, createdAt: user.createdAt,
    };
  }

  private assertValidRole(role: string): void {
    if (!(VALID_ROLES as readonly string[]).includes(role)) {
      throw new BadRequestException(`Invalid role. Must be one of: ${VALID_ROLES.join(', ')}`);
    }
  }

  async createUser(email: string, password: string, role: string): Promise<AdminUserView> {
    this.assertValidRole(role);
    const normalized = email.trim().toLowerCase();
    const existing = await this.usersRepository.findOne({ where: { email: normalized } });
    if (existing) {
      throw new ConflictException('A user with that email already exists');
    }
    const passwordHash = await bcrypt.hash(password, BCRYPT_ROUNDS);
    const user = await this.usersRepository.save(
      this.usersRepository.create({ email: normalized, passwordHash, role, isActive: true }),
    );
    return this.toView(user);
  }

  async updateRole(actorId: string, targetId: string, role: string): Promise<AdminUserView> {
    this.assertValidRole(role);
    const user = await this.findOrThrow(targetId);
    // Prevent an admin from demoting themselves out of admin (self-lockout).
    if (actorId === targetId && user.role === 'admin' && role !== 'admin') {
      throw new BadRequestException("You can't remove your own admin role");
    }
    user.role = role;
    return this.toView(await this.usersRepository.save(user));
  }

  async setActive(actorId: string, targetId: string, isActive: boolean): Promise<AdminUserView> {
    const user = await this.findOrThrow(targetId);
    if (actorId === targetId && !isActive) {
      throw new BadRequestException("You can't disable your own account");
    }
    user.isActive = isActive;
    return this.toView(await this.usersRepository.save(user));
  }

  async resetPassword(targetId: string, password: string): Promise<void> {
    const user = await this.findOrThrow(targetId);
    user.passwordHash = await bcrypt.hash(password, BCRYPT_ROUNDS);
    await this.usersRepository.save(user);
  }

  private async findOrThrow(id: string): Promise<User> {
    const user = await this.usersRepository.findOne({ where: { id } });
    if (!user) throw new NotFoundException('User not found');
    return user;
  }
}
