import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import { AuditLogService } from './audit-log.service';
import { AuditLog } from './entities/audit-log.entity';

describe('AuditLogService', () => {
  let service: AuditLogService;
  let repo: any;

  beforeEach(async () => {
    repo = {
      create: jest.fn((dto) => dto),
      save: jest.fn((entity) => Promise.resolve({ id: 'log-uuid', ...entity })),
    };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        AuditLogService,
        { provide: getRepositoryToken(AuditLog), useValue: repo },
      ],
    }).compile();

    service = module.get<AuditLogService>(AuditLogService);
  });

  it('should create and save an audit log entry', async () => {
    await service.log('login', 'user-1', '127.0.0.1', { extra: 'data' });

    expect(repo.create).toHaveBeenCalledWith({
      eventType: 'login',
      userId: 'user-1',
      ipAddress: '127.0.0.1',
      details: { extra: 'data' },
    });
    expect(repo.save).toHaveBeenCalled();
  });

  it('should handle null details', async () => {
    await service.log('logout', 'user-1', '10.0.0.1');

    expect(repo.create).toHaveBeenCalledWith({
      eventType: 'logout',
      userId: 'user-1',
      ipAddress: '10.0.0.1',
      details: null,
    });
  });

  it('should not throw when save fails', async () => {
    repo.save.mockRejectedValue(new Error('DB error'));

    await expect(
      service.log('login', 'user-1', '127.0.0.1'),
    ).resolves.toBeUndefined();
  });
});
