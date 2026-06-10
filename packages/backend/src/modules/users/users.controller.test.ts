import { Test, TestingModule } from '@nestjs/testing';
import { UsersController } from './users.controller';
import { UsersService } from './users.service';

describe('UsersController', () => {
  let controller: UsersController;
  let mockService: Record<string, jest.Mock>;

  const mockProfile = {
    id: 'user-uuid-1',
    email: 'trader@example.com',
    role: 'trader',
    createdAt: new Date('2024-01-01'),
    updatedAt: new Date('2024-01-01'),
  };

  beforeEach(async () => {
    mockService = {
      getProfile: jest.fn().mockResolvedValue(mockProfile),
      updateProfile: jest.fn().mockResolvedValue(mockProfile),
      getById: jest.fn().mockResolvedValue(mockProfile),
    };

    const module: TestingModule = await Test.createTestingModule({
      controllers: [UsersController],
      providers: [{ provide: UsersService, useValue: mockService }],
    }).compile();

    controller = module.get<UsersController>(UsersController);
  });

  describe('GET /users/me', () => {
    it('should call getProfile with the authenticated user id', async () => {
      const req = { user: { id: 'user-uuid-1', email: 'trader@example.com', role: 'trader' } };

      const result = await controller.getProfile(req);

      expect(mockService.getProfile).toHaveBeenCalledWith('user-uuid-1');
      expect(result).toEqual(mockProfile);
    });
  });

  describe('PUT /users/me', () => {
    it('should call updateProfile with the authenticated user id and dto', async () => {
      const req = { user: { id: 'user-uuid-1' } };
      const dto = { email: 'new@example.com' };

      await controller.updateProfile(req, dto);

      expect(mockService.updateProfile).toHaveBeenCalledWith('user-uuid-1', dto);
    });
  });

  describe('GET /users/:id', () => {
    it('should call getById with the provided id', async () => {
      const result = await controller.getById('some-admin-target-id');

      expect(mockService.getById).toHaveBeenCalledWith('some-admin-target-id');
      expect(result).toEqual(mockProfile);
    });
  });
});
