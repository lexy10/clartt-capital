import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import { HttpService } from '@nestjs/axios';
import { AdminService } from './admin.service';
import { KillSwitch } from './entities/kill-switch.entity';
import { REDIS_CLIENT } from '../../common/modules/redis.module';
import { AccountsService } from '../accounts/accounts.service';
import { EventPublisherService } from '../events/event-publisher.service';
import { TradingEventType } from '../events/enums/trading-event-type.enum';

describe('AdminService', () => {
  let service: AdminService;
  let mockRepo: Record<string, jest.Mock>;
  let mockRedis: Record<string, jest.Mock>;
  let mockEventPublisher: Record<string, jest.Mock>;
  let mockHttpService: Record<string, jest.Mock>;
  let mockAccountsService: Record<string, jest.Mock>;

  beforeEach(async () => {
    mockRepo = {
      findOne: jest.fn(),
      create: jest.fn(),
      save: jest.fn(),
    };

    mockRedis = {
      set: jest.fn().mockResolvedValue('OK'),
      publish: jest.fn().mockResolvedValue(1),
    };

    mockEventPublisher = {
      publish: jest.fn().mockResolvedValue(undefined),
    };

    mockHttpService = {
      post: jest.fn(),
    };

    mockAccountsService = {
      findAllActive: jest.fn().mockResolvedValue([]),
    };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        AdminService,
        { provide: getRepositoryToken(KillSwitch), useValue: mockRepo },
        { provide: REDIS_CLIENT, useValue: mockRedis },
        { provide: HttpService, useValue: mockHttpService },
        { provide: AccountsService, useValue: mockAccountsService },
        { provide: EventPublisherService, useValue: mockEventPublisher },
      ],
    }).compile();

    service = module.get<AdminService>(AdminService);
  });

  describe('activateKillSwitch', () => {
    it('should activate kill switch and persist to DB and Redis', async () => {
      const existing = { id: 1, isActive: false, activatedBy: null, activatedAt: null, deactivatedAt: null };
      mockRepo.findOne.mockResolvedValue(existing);
      mockRepo.save.mockImplementation((ks: any) => Promise.resolve(ks));

      const result = await service.activateKillSwitch('admin-uuid');

      expect(result.isActive).toBe(true);
      expect(result.activatedBy).toBe('admin-uuid');
      expect(result.activatedAt).toBeInstanceOf(Date);
      expect(result.deactivatedAt).toBeNull();
      expect(mockRedis.set).toHaveBeenCalledWith('kill_switch:status', 'active');
      expect(mockRedis.publish).toHaveBeenCalledWith('kill_switch:channel', 'active');
    });

    it('should create kill switch row if none exists', async () => {
      mockRepo.findOne.mockResolvedValue(null);
      mockRepo.create.mockReturnValue({ id: 1 });
      mockRepo.save.mockImplementation((ks: any) => Promise.resolve(ks));

      const result = await service.activateKillSwitch('admin-uuid');

      expect(mockRepo.create).toHaveBeenCalledWith({ id: 1 });
      expect(result.isActive).toBe(true);
    });

    it('should publish KillSwitchActivated event (fire-and-forget)', async () => {
      const existing = { id: 1, isActive: false, activatedBy: null, activatedAt: null, deactivatedAt: null };
      mockRepo.findOne.mockResolvedValue(existing);
      mockRepo.save.mockImplementation((ks: any) => Promise.resolve(ks));

      await service.activateKillSwitch('admin-uuid', 'hard');

      // Allow fire-and-forget promise to resolve
      await new Promise((r) => setTimeout(r, 10));

      expect(mockEventPublisher.publish).toHaveBeenCalledWith(
        expect.objectContaining({
          event_type: TradingEventType.KillSwitchActivated,
          aggregate_id: 'kill_switch:1',
          payload: expect.objectContaining({
            activated_by: 'admin-uuid',
            reason: 'hard',
          }),
        }),
      );
    });
  });

  describe('deactivateKillSwitch', () => {
    it('should deactivate kill switch and persist to DB and Redis', async () => {
      const existing = { id: 1, isActive: true, activatedBy: 'admin-uuid', activatedAt: new Date(), deactivatedAt: null };
      mockRepo.findOne.mockResolvedValue(existing);
      mockRepo.save.mockImplementation((ks: any) => Promise.resolve(ks));

      const result = await service.deactivateKillSwitch('admin-uuid');

      expect(result.isActive).toBe(false);
      expect(result.activatedBy).toBeNull();
      expect(result.deactivatedAt).toBeInstanceOf(Date);
      expect(mockRedis.set).toHaveBeenCalledWith('kill_switch:status', 'inactive');
      expect(mockRedis.publish).toHaveBeenCalledWith('kill_switch:channel', 'inactive');
    });

    it('should publish KillSwitchDeactivated event with duration', async () => {
      const activatedAt = new Date(Date.now() - 60000); // 60 seconds ago
      const existing = { id: 1, isActive: true, activatedBy: 'admin-uuid', activatedAt, deactivatedAt: null };
      mockRepo.findOne.mockResolvedValue(existing);
      mockRepo.save.mockImplementation((ks: any) => Promise.resolve(ks));

      await service.deactivateKillSwitch('admin-uuid');

      // Allow fire-and-forget promise to resolve
      await new Promise((r) => setTimeout(r, 10));

      expect(mockEventPublisher.publish).toHaveBeenCalledWith(
        expect.objectContaining({
          event_type: TradingEventType.KillSwitchDeactivated,
          aggregate_id: 'kill_switch:1',
          payload: expect.objectContaining({
            deactivated_by: 'admin-uuid',
            duration_active_seconds: expect.any(Number),
          }),
        }),
      );

      const publishedPayload = mockEventPublisher.publish.mock.calls[0][0].payload;
      expect(publishedPayload.duration_active_seconds).toBeGreaterThanOrEqual(59);
      expect(publishedPayload.duration_active_seconds).toBeLessThanOrEqual(61);
    });
  });

  describe('getStatus', () => {
    it('should return kill switch state and system info', async () => {
      const existing = { id: 1, isActive: true, activatedBy: 'admin-uuid', activatedAt: new Date(), deactivatedAt: null };
      mockRepo.findOne.mockResolvedValue(existing);

      const result = await service.getStatus();

      expect(result.killSwitch.isActive).toBe(true);
      expect(result.killSwitch.activatedBy).toBe('admin-uuid');
      expect(result.system.uptime).toBeGreaterThan(0);
      expect(result.system.timestamp).toBeDefined();
    });

    it('should create default kill switch row if none exists', async () => {
      mockRepo.findOne.mockResolvedValue(null);
      mockRepo.create.mockReturnValue({ id: 1, isActive: false, activatedBy: null, activatedAt: null, deactivatedAt: null });
      mockRepo.save.mockImplementation((ks: any) => Promise.resolve(ks));

      const result = await service.getStatus();

      expect(result.killSwitch.isActive).toBe(false);
      expect(mockRepo.create).toHaveBeenCalled();
      expect(mockRepo.save).toHaveBeenCalled();
    });
  });
});
