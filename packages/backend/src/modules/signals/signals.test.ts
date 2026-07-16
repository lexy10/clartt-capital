import { Test, TestingModule } from '@nestjs/testing';
import { ValidationPipe } from '@nestjs/common';
import { getRepositoryToken } from '@nestjs/typeorm';
import { SignalsService } from './signals.service';
import { SignalsController } from './signals.controller';
import { Signal } from './entities/signal.entity';
import { Trade } from '../trades/entities/trade.entity';
import { CreateSignalDto } from './dto/create-signal.dto';
import { validate } from 'class-validator';
import { plainToInstance } from 'class-transformer';

describe('Signals Module', () => {
  let controller: SignalsController;
  let service: SignalsService;
  let mockRepo: Record<string, jest.Mock>;

  const validPayload: CreateSignalDto = {
    instrument: 'US30',
    direction: 'BUY',
    entryPrice: 38505.0,
    stopLoss: 38490.0,
    takeProfit: 38535.0,
    positionSize: 0.05,
    confidenceScore: 0.82,
    timeframe: 'M15',
    orderBlockId: 'ob-uuid-123',
    strategyId: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
    mode: 'live',
    metadata: { bos_type: 'bullish' },
  };

  beforeEach(async () => {
    mockRepo = {
      create: jest.fn((data: any) => ({ id: 'signal-uuid-1', createdAt: new Date(), ...data })),
      save: jest.fn((entity: any) => Promise.resolve({ ...entity })),
      findAndCount: jest.fn().mockResolvedValue([[], 0]),
      findOne: jest.fn().mockResolvedValue(null),
    };

    // Trade repo: the service uses a query builder to find signal ids with trades.
    const mockTradeRepo = {
      createQueryBuilder: jest.fn(() => ({
        select: jest.fn().mockReturnThis(),
        where: jest.fn().mockReturnThis(),
        getRawMany: jest.fn().mockResolvedValue([]),
      })),
    };

    const module: TestingModule = await Test.createTestingModule({
      controllers: [SignalsController],
      providers: [
        SignalsService,
        { provide: getRepositoryToken(Signal), useValue: mockRepo },
        { provide: getRepositoryToken(Trade), useValue: mockTradeRepo },
      ],
    }).compile();

    controller = module.get<SignalsController>(SignalsController);
    service = module.get<SignalsService>(SignalsService);
  });

  describe('POST /signals - valid payload creates record', () => {
    it('should create a signal with all fields converted correctly', async () => {
      const result = await controller.create(validPayload);

      expect(mockRepo.create).toHaveBeenCalledWith({
        instrument: 'US30',
        direction: 'BUY',
        entryPrice: '38505',
        stopLoss: '38490',
        takeProfit: '38535',
        positionSize: '0.05',
        confidenceScore: '0.82',
        timeframe: 'M15',
        orderBlockId: 'ob-uuid-123',
        strategyId: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
        mode: 'live',
        metadata: { bos_type: 'bullish' },
      });
      expect(mockRepo.save).toHaveBeenCalled();
      expect(result).toHaveProperty('id');
      expect(result.instrument).toBe('US30');
    });
  });

  describe('POST /signals - missing fields returns validation errors', () => {
    it('should fail validation when required fields are missing', async () => {
      const incomplete = plainToInstance(CreateSignalDto, { instrument: 'US30' });
      const errors = await validate(incomplete);
      expect(errors.length).toBeGreaterThan(0);

      const errorProperties = errors.map((e) => e.property);
      expect(errorProperties).toContain('direction');
      expect(errorProperties).toContain('entryPrice');
      expect(errorProperties).toContain('stopLoss');
      expect(errorProperties).toContain('takeProfit');
      expect(errorProperties).toContain('positionSize');
      expect(errorProperties).toContain('confidenceScore');
      expect(errorProperties).toContain('timeframe');
      expect(errorProperties).toContain('mode');
    });

    it('should fail validation for invalid direction', async () => {
      const bad = plainToInstance(CreateSignalDto, { ...validPayload, direction: 'HOLD' });
      const errors = await validate(bad);
      const dirError = errors.find((e) => e.property === 'direction');
      expect(dirError).toBeDefined();
    });

    it('should fail validation for invalid mode', async () => {
      const bad = plainToInstance(CreateSignalDto, { ...validPayload, mode: 'paper' });
      const errors = await validate(bad);
      const modeError = errors.find((e) => e.property === 'mode');
      expect(modeError).toBeDefined();
    });

    it('should fail validation for confidenceScore out of range', async () => {
      const bad = plainToInstance(CreateSignalDto, { ...validPayload, confidenceScore: 1.5 });
      const errors = await validate(bad);
      const csError = errors.find((e) => e.property === 'confidenceScore');
      expect(csError).toBeDefined();
    });
  });

  describe('POST /signals - null strategyId succeeds', () => {
    it('should create a signal when strategyId is undefined', async () => {
      const { strategyId, ...payloadWithoutStrategy } = validPayload;
      const result = await controller.create(payloadWithoutStrategy as CreateSignalDto);

      expect(mockRepo.create).toHaveBeenCalledWith(
        expect.objectContaining({ strategyId: null }),
      );
      expect(mockRepo.save).toHaveBeenCalled();
      expect(result).toHaveProperty('id');
    });

    it('should pass validation when strategyId is omitted', async () => {
      const { strategyId, ...payload } = validPayload;
      const dto = plainToInstance(CreateSignalDto, payload);
      const errors = await validate(dto);
      expect(errors.length).toBe(0);
    });
  });

  describe('GET /signals - returns signals created via POST', () => {
    it('should return signals that were created', async () => {
      const savedSignal = {
        id: 'signal-uuid-1',
        instrument: 'US30',
        direction: 'BUY',
        entryPrice: '38505',
        stopLoss: '38490',
        takeProfit: '38535',
        positionSize: '0.05',
        confidenceScore: '0.82',
        timeframe: 'M15',
        orderBlockId: 'ob-uuid-123',
        strategyId: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
        mode: 'live',
        metadata: { bos_type: 'bullish' },
        createdAt: new Date(),
      };

      mockRepo.findAndCount.mockResolvedValue([[savedSignal], 1]);

      const result = await controller.findAll();

      expect(result.data).toHaveLength(1);
      expect(result.data[0].id).toBe('signal-uuid-1');
      expect(result.data[0].instrument).toBe('US30');
      expect(result.total).toBe(1);
    });
  });
});
