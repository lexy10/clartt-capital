import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository, SelectQueryBuilder } from 'typeorm';
import { TradingEvent } from './entities/trading-event.entity';
import { EventQueryDto } from './dto/event-query.dto';
import { EventStoreMetrics } from './event-store-metrics';

export interface PaginatedResult<T> {
  events: T[];
  total_count: number;
  current_page: number;
  page_size: number;
  total_pages: number;
}

@Injectable()
export class EventQueryService {
  private readonly logger = new Logger(EventQueryService.name);

  constructor(
    @InjectRepository(TradingEvent)
    private readonly eventRepo: Repository<TradingEvent>,
    private readonly metrics: EventStoreMetrics,
  ) {}

  async findEvents(
    filters: EventQueryDto,
  ): Promise<PaginatedResult<TradingEvent>> {
    const startTime = process.hrtime.bigint();

    try {
      const page = filters.page ?? 1;
      const pageSize = Math.min(filters.page_size ?? 50, 200);
      const sort = filters.sort ?? 'desc';
      const includeArchived = filters.include_archived ?? false;

      if (includeArchived) {
        return await this.findEventsWithArchive(
          filters,
          page,
          pageSize,
          sort,
        );
      }

      const qb = this.eventRepo.createQueryBuilder('e');
      this.applyFilters(qb, filters);

      qb.orderBy(
        'e.createdAt',
        sort === 'asc' ? 'ASC' : 'DESC',
      );

      qb.skip((page - 1) * pageSize).take(pageSize);

      const [events, totalCount] = await qb.getManyAndCount();

      return {
        events,
        total_count: totalCount,
        current_page: page,
        page_size: pageSize,
        total_pages: Math.ceil(totalCount / pageSize) || 1,
      };
    } finally {
      const durationNs = Number(process.hrtime.bigint() - startTime);
      this.metrics.observeQueryDuration('list', durationNs / 1e9);
    }
  }

  async findByAggregate(aggregateId: string): Promise<TradingEvent[]> {
    const startTime = process.hrtime.bigint();

    try {
      return await this.eventRepo.find({
        where: { aggregateId },
        order: { sequenceNumber: 'ASC' },
      });
    } finally {
      const durationNs = Number(process.hrtime.bigint() - startTime);
      this.metrics.observeQueryDuration('aggregate', durationNs / 1e9);
    }
  }

  private applyFilters(
    qb: SelectQueryBuilder<TradingEvent>,
    filters: EventQueryDto,
  ): void {
    if (filters.account_id) {
      qb.andWhere("e.payload->>'account_id' = :accountId", {
        accountId: filters.account_id,
      });
    }

    if (filters.instrument) {
      qb.andWhere("e.payload->>'instrument' = :instrument", {
        instrument: filters.instrument,
      });
    }

    if (filters.event_type) {
      qb.andWhere('e.eventType = :eventType', {
        eventType: filters.event_type,
      });
    }

    if (filters.correlation_id) {
      qb.andWhere('e.correlationId = :correlationId', {
        correlationId: filters.correlation_id,
      });
    }

    if (filters.aggregate_id) {
      qb.andWhere('e.aggregateId = :aggregateId', {
        aggregateId: filters.aggregate_id,
      });
    }

    if (filters.start_time) {
      qb.andWhere('e.createdAt >= :startTime', {
        startTime: new Date(filters.start_time),
      });
    }

    if (filters.end_time) {
      qb.andWhere('e.createdAt <= :endTime', {
        endTime: new Date(filters.end_time),
      });
    }
  }

  /**
   * Query both trading_events and trading_events_archive using UNION ALL.
   */
  private async findEventsWithArchive(
    filters: EventQueryDto,
    page: number,
    pageSize: number,
    sort: 'asc' | 'desc',
  ): Promise<PaginatedResult<TradingEvent>> {
    const manager = this.eventRepo.manager;
    const params: any[] = [];
    let paramIndex = 0;

    const buildWhereClause = (alias: string): string => {
      const conditions: string[] = [];

      if (filters.account_id) {
        paramIndex++;
        conditions.push(`${alias}.payload->>'account_id' = $${paramIndex}`);
        params.push(filters.account_id);
      }

      if (filters.instrument) {
        paramIndex++;
        conditions.push(`${alias}.payload->>'instrument' = $${paramIndex}`);
        params.push(filters.instrument);
      }

      if (filters.event_type) {
        paramIndex++;
        conditions.push(`${alias}.event_type = $${paramIndex}`);
        params.push(filters.event_type);
      }

      if (filters.correlation_id) {
        paramIndex++;
        conditions.push(`${alias}.correlation_id = $${paramIndex}`);
        params.push(filters.correlation_id);
      }

      if (filters.aggregate_id) {
        paramIndex++;
        conditions.push(`${alias}.aggregate_id = $${paramIndex}`);
        params.push(filters.aggregate_id);
      }

      if (filters.start_time) {
        paramIndex++;
        conditions.push(`${alias}.created_at >= $${paramIndex}`);
        params.push(new Date(filters.start_time));
      }

      if (filters.end_time) {
        paramIndex++;
        conditions.push(`${alias}.created_at <= $${paramIndex}`);
        params.push(new Date(filters.end_time));
      }

      return conditions.length > 0
        ? 'WHERE ' + conditions.join(' AND ')
        : '';
    };

    // Build WHERE clauses — both tables use the same filters,
    // but we need separate parameter indices for each.
    // Reset paramIndex for the first table.
    paramIndex = 0;
    const whereActive = buildWhereClause('t');
    const activeParams = [...params];

    // Reset for archive table — reuse same param values but new indices.
    params.length = 0;
    paramIndex = activeParams.length;
    const whereArchive = buildWhereClause('t');
    const archiveParams = [...params];

    const allParams = [...activeParams, ...archiveParams];

    const columns = `t.id, t.event_type AS "eventType", t.aggregate_id AS "aggregateId", t.sequence_number AS "sequenceNumber", t.correlation_id AS "correlationId", t.payload, t.context_snapshot AS "contextSnapshot", t.source_service AS "sourceService", t.created_at AS "createdAt", t.schema_version AS "schemaVersion"`;

    const unionQuery = `
      SELECT ${columns} FROM trading_events t ${whereActive}
      UNION ALL
      SELECT ${columns} FROM trading_events_archive t ${whereArchive}
    `;

    const orderDir = sort === 'asc' ? 'ASC' : 'DESC';
    const offset = (page - 1) * pageSize;

    // Count query
    const countSql = `SELECT COUNT(*) AS count FROM (${unionQuery}) AS combined`;
    const countResult = await manager.query(countSql, allParams);
    const totalCount = parseInt(countResult[0]?.count ?? '0', 10);

    // Data query with pagination
    const dataSql = `${unionQuery} ORDER BY "createdAt" ${orderDir} LIMIT ${pageSize} OFFSET ${offset}`;
    const rows = await manager.query(dataSql, allParams);

    // Map raw rows to TradingEvent entities
    const events: TradingEvent[] = rows.map((row: any) => {
      const event = new TradingEvent();
      event.id = row.id;
      event.eventType = row.eventType;
      event.aggregateId = row.aggregateId;
      event.sequenceNumber = row.sequenceNumber;
      event.correlationId = row.correlationId;
      event.payload = row.payload;
      event.contextSnapshot = row.contextSnapshot;
      event.sourceService = row.sourceService;
      event.createdAt = row.createdAt instanceof Date ? row.createdAt : new Date(row.createdAt);
      event.schemaVersion = row.schemaVersion;
      return event;
    });

    return {
      events,
      total_count: totalCount,
      current_page: page,
      page_size: pageSize,
      total_pages: Math.ceil(totalCount / pageSize) || 1,
    };
  }
}
