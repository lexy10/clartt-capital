import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import { NotFoundException } from '@nestjs/common';
import { UsersService } from './users.service';
import { User } from '../auth/entities/user.entity';

describe('UsersService', () => {
  let service: UsersService;
  let mockRepository: Record<string, jest.Mock>;

  const mockUser: User = {
    id: 'user-uuid-1',
    email: 'trader@example.com',
    passwordHash: 'hashed-password',
    role: 'trader',
    isActive: true,
    theme: null,
    createdAt: new Date('2024-01-01'),
    updatedAt: new Date('2024-01-01'),
    refreshTokens: [],
  };

  beforeEach(async () => {
    mockRepository = {
      findOne: jest.fn(),
      save: jest.fn(),
    };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        UsersService,
        {
          provide: getRepositoryToken(User),
          useValue: mockRepository,
        },
      ],
    }).compile();

    service = module.get<UsersService>(UsersService);
  });

  describe('getProfile', () => {
    it('should return user profile without passwordHash and refreshTokens', async () => {
      mockRepository.findOne.mockResolvedValue(mockUser);

      const result = await service.getProfile('user-uuid-1');

      expect(result).toEqual({
        id: 'user-uuid-1',
        email: 'trader@example.com',
        role: 'trader',
        isActive: true,
        theme: null,
        createdAt: mockUser.createdAt,
        updatedAt: mockUser.updatedAt,
      });
      expect(result).not.toHaveProperty('passwordHash');
      expect(result).not.toHaveProperty('refreshTokens');
    });

    it('should throw NotFoundException when user does not exist', async () => {
      mockRepository.findOne.mockResolvedValue(null);

      await expect(service.getProfile('nonexistent')).rejects.toThrow(
        NotFoundException,
      );
    });
  });

  describe('updateProfile', () => {
    it('should update email and return profile without sensitive fields', async () => {
      const updatedUser = { ...mockUser, email: 'new@example.com' };
      mockRepository.findOne.mockResolvedValue({ ...mockUser });
      mockRepository.save.mockResolvedValue(updatedUser);

      const result = await service.updateProfile('user-uuid-1', {
        email: 'new@example.com',
      });

      expect(result.email).toBe('new@example.com');
      expect(result).not.toHaveProperty('passwordHash');
    });

    it('should not modify user when dto is empty', async () => {
      mockRepository.findOne.mockResolvedValue({ ...mockUser });
      mockRepository.save.mockResolvedValue(mockUser);

      const result = await service.updateProfile('user-uuid-1', {});

      expect(result.email).toBe('trader@example.com');
    });

    it('should throw NotFoundException when user does not exist', async () => {
      mockRepository.findOne.mockResolvedValue(null);

      await expect(
        service.updateProfile('nonexistent', { email: 'a@b.com' }),
      ).rejects.toThrow(NotFoundException);
    });
  });

  describe('getById', () => {
    it('should return user profile by id', async () => {
      mockRepository.findOne.mockResolvedValue(mockUser);

      const result = await service.getById('user-uuid-1');

      expect(result.id).toBe('user-uuid-1');
      expect(result).not.toHaveProperty('passwordHash');
    });

    it('should throw NotFoundException when user does not exist', async () => {
      mockRepository.findOne.mockResolvedValue(null);

      await expect(service.getById('nonexistent')).rejects.toThrow(
        NotFoundException,
      );
    });
  });
});
