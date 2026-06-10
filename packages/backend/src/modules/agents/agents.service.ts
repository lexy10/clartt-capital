import {
  Injectable,
  Inject,
  Logger,
  OnModuleInit,
  OnModuleDestroy,
} from '@nestjs/common';
import Redis from 'ioredis';
import { REDIS_CLIENT } from '../../common/modules/redis.module';
import { AgentsGateway } from './agents.gateway';

const AGENTS_EVENTS_STREAM = 'agents:events';
const AGENTS_EVENTS_GROUP = 'backend-agents';
const AGENTS_EVENTS_CONSUMER = 'agents-consumer';
const AGENTS_ACTIVITY_CHANNEL = 'agents:activity';

@Injectable()
export class AgentsService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(AgentsService.name);
  private subscriberClient: Redis | null = null;
  private running = false;
  private pollTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
    private readonly gateway: AgentsGateway,
  ) {}

  async onModuleInit(): Promise<void> {
    await this.ensureConsumerGroup();
    this.startStreamPoller();
    this.startActivitySubscriber();
    this.logger.log('Agents Redis listeners initialized');
  }

  onModuleDestroy(): void {
    this.running = false;
    if (this.pollTimer) {
      clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
    if (this.subscriberClient) {
      this.subscriberClient.disconnect();
      this.subscriberClient = null;
    }
    this.logger.log('Agents Redis listeners stopped');
  }

  private async ensureConsumerGroup(): Promise<void> {
    try {
      await this.redis.xgroup(
        'CREATE',
        AGENTS_EVENTS_STREAM,
        AGENTS_EVENTS_GROUP,
        '$',
        'MKSTREAM',
      );
      this.logger.log(
        `Created consumer group "${AGENTS_EVENTS_GROUP}" on "${AGENTS_EVENTS_STREAM}"`,
      );
    } catch (err: any) {
      if (err?.message?.includes('BUSYGROUP')) {
        this.logger.debug(`Consumer group "${AGENTS_EVENTS_GROUP}" already exists`);
      } else {
        this.logger.error(`Failed to create consumer group: ${err?.message}`);
      }
    }
  }

  private startStreamPoller(): void {
    this.running = true;
    this.pollEvents();
  }

  private async pollEvents(): Promise<void> {
    if (!this.running) return;

    try {
      const results = (await this.redis.xreadgroup(
        'GROUP',
        AGENTS_EVENTS_GROUP,
        AGENTS_EVENTS_CONSUMER,
        'COUNT',
        '10',
        'BLOCK',
        '2000',
        'STREAMS',
        AGENTS_EVENTS_STREAM,
        '>',
      )) as [string, [string, string[]][]][] | null;

      if (results) {
        for (const [, messages] of results) {
          for (const [messageId, fields] of messages) {
            this.handleStreamEvent(messageId, fields);
          }
        }
      }
    } catch (err: any) {
      if (this.running) {
        this.logger.error(`Error polling agents:events stream: ${err?.message}`);
      }
    }

    if (this.running) {
      this.pollTimer = setTimeout(() => this.pollEvents(), 50);
    }
  }

  private handleStreamEvent(messageId: string, fields: string[]): void {
    try {
      const dataIndex = fields.indexOf('data');
      if (dataIndex === -1 || dataIndex + 1 >= fields.length) {
        this.logger.warn(`Agent event ${messageId} missing "data" field`);
        this.ackMessage(messageId);
        return;
      }

      const event = JSON.parse(fields[dataIndex + 1]);
      const eventType: string = event.event_type || event.eventType || '';

      if (eventType.includes('LifecycleChanged') || eventType.includes('StateChange')) {
        this.gateway.emitAgentStateChange(event);
      } else if (eventType.includes('Task')) {
        this.gateway.emitAgentTaskUpdate(event);
      } else if (eventType.includes('Pipeline')) {
        this.gateway.emitAgentPipelineUpdate(event);
      } else if (eventType.includes('Error') || eventType.includes('Failed')) {
        this.gateway.emitAgentError(event);
      }

      this.ackMessage(messageId);
    } catch (err: any) {
      this.logger.error(
        `Failed to process agent event ${messageId}: ${err?.message}`,
      );
      this.ackMessage(messageId);
    }
  }

  private ackMessage(messageId: string): void {
    this.redis
      .xack(AGENTS_EVENTS_STREAM, AGENTS_EVENTS_GROUP, messageId)
      .catch((err) => {
        this.logger.error(`Failed to ACK agent event ${messageId}: ${err?.message}`);
      });
  }

  private startActivitySubscriber(): void {
    this.subscriberClient = this.redis.duplicate();

    this.subscriberClient.subscribe(AGENTS_ACTIVITY_CHANNEL, (err) => {
      if (err) {
        this.logger.error(
          `Failed to subscribe to ${AGENTS_ACTIVITY_CHANNEL}: ${err.message}`,
        );
      } else {
        this.logger.log(`Subscribed to pub/sub channel: ${AGENTS_ACTIVITY_CHANNEL}`);
      }
    });

    this.subscriberClient.on('message', (channel: string, message: string) => {
      if (channel === AGENTS_ACTIVITY_CHANNEL) {
        this.handleActivityMessage(message);
      }
    });
  }

  private handleActivityMessage(message: string): void {
    try {
      const event = JSON.parse(message);
      const eventType: string = event.event_type || event.eventType || event.type || '';

      if (eventType.includes('Approval') && eventType.includes('Requested')) {
        this.gateway.emitApprovalRequested(event);
      } else if (eventType.includes('Approval') && (eventType.includes('Resolved') || eventType.includes('Granted') || eventType.includes('Denied'))) {
        this.gateway.emitApprovalResolved(event);
      } else if (eventType.includes('KillSwitch') || eventType.includes('kill_switch')) {
        this.gateway.emitKillSwitch(event);
      } else {
        this.gateway.emitAgentActivity(event);
      }
    } catch (err: any) {
      this.logger.error(`Failed to process activity message: ${err?.message}`);
    }
  }
}
