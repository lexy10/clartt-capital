import { Controller, Get, Header } from '@nestjs/common';
import * as client from 'prom-client';

@Controller('metrics')
export class MetricsController {
  @Get()
  @Header('Content-Type', client.register.contentType)
  async getMetrics(): Promise<string> {
    return client.register.metrics();
  }
}
