import {
  WebSocketGateway,
  WebSocketServer,
  SubscribeMessage,
  ConnectedSocket,
  OnGatewayConnection,
  OnGatewayDisconnect,
} from '@nestjs/websockets';
import { Logger } from '@nestjs/common';
import { Server, Socket } from 'socket.io';

const AGENTS_ROOM = 'agents';

@WebSocketGateway({ cors: { origin: '*' } })
export class AgentsGateway implements OnGatewayConnection, OnGatewayDisconnect {
  private readonly logger = new Logger(AgentsGateway.name);

  @WebSocketServer()
  server: Server;

  handleConnection(client: Socket): void {
    this.logger.debug(`Client connected: ${client.id}`);
  }

  handleDisconnect(client: Socket): void {
    this.logger.debug(`Client disconnected: ${client.id}`);
  }

  @SubscribeMessage('subscribeAgents')
  handleSubscribeAgents(@ConnectedSocket() client: Socket): void {
    client.join(AGENTS_ROOM);
    this.logger.log(`Client ${client.id} subscribed to agents`);
  }

  // --- Emitters called by AgentsService ---

  emitAgentStateChange(payload: Record<string, unknown>): void {
    this.server.to(AGENTS_ROOM).emit('agent:stateChange', payload);
  }

  emitAgentTaskUpdate(payload: Record<string, unknown>): void {
    this.server.to(AGENTS_ROOM).emit('agent:taskUpdate', payload);
  }

  emitAgentPipelineUpdate(payload: Record<string, unknown>): void {
    this.server.to(AGENTS_ROOM).emit('agent:pipelineUpdate', payload);
  }

  emitApprovalRequested(payload: Record<string, unknown>): void {
    this.server.to(AGENTS_ROOM).emit('agent:approvalRequested', payload);
  }

  emitApprovalResolved(payload: Record<string, unknown>): void {
    this.server.to(AGENTS_ROOM).emit('agent:approvalResolved', payload);
  }

  emitAgentError(payload: Record<string, unknown>): void {
    this.server.to(AGENTS_ROOM).emit('agent:error', payload);
  }

  emitKillSwitch(payload: Record<string, unknown>): void {
    this.server.to(AGENTS_ROOM).emit('agent:killSwitch', payload);
  }

  emitAgentActivity(payload: Record<string, unknown>): void {
    this.server.to(AGENTS_ROOM).emit('agent:activity', payload);
  }
}
