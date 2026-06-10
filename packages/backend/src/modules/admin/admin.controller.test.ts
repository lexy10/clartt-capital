import { AdminController } from './admin.controller';
import { AdminService } from './admin.service';

describe('AdminController', () => {
  let controller: AdminController;
  let mockService: Record<string, jest.Mock>;

  beforeEach(() => {
    mockService = {
      activateKillSwitch: jest.fn(),
      deactivateKillSwitch: jest.fn(),
      getStatus: jest.fn(),
    };
    controller = new AdminController(mockService as any);
  });

  describe('POST /admin/kill-switch', () => {
    it('should call activateKillSwitch when active is true', async () => {
      const req = { user: { id: 'admin-uuid' } };
      mockService.activateKillSwitch.mockResolvedValue({ isActive: true });

      const result = await controller.toggleKillSwitch(req, { active: true });

      expect(mockService.activateKillSwitch).toHaveBeenCalledWith('admin-uuid', 'soft');
      expect(result.isActive).toBe(true);
    });

    it('should call deactivateKillSwitch when active is false', async () => {
      const req = { user: { id: 'admin-uuid' } };
      mockService.deactivateKillSwitch.mockResolvedValue({ isActive: false });

      const result = await controller.toggleKillSwitch(req, { active: false });

      expect(mockService.deactivateKillSwitch).toHaveBeenCalledWith('admin-uuid');
      expect(result.isActive).toBe(false);
    });
  });

  describe('GET /admin/status', () => {
    it('should return system status', async () => {
      const status = {
        killSwitch: { isActive: false, activatedBy: null, activatedAt: null, deactivatedAt: null },
        system: { uptime: 100, timestamp: '2024-01-01T00:00:00.000Z' },
      };
      mockService.getStatus.mockResolvedValue(status);

      const result = await controller.getStatus();

      expect(mockService.getStatus).toHaveBeenCalled();
      expect(result).toEqual(status);
    });
  });
});
