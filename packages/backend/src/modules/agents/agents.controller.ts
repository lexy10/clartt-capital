import {
  Controller,
  All,
  Req,
  Res,
  HttpStatus,
  Logger,
} from '@nestjs/common';
import { HttpService } from '@nestjs/axios';
import { Request, Response } from 'express';
import { firstValueFrom } from 'rxjs';

const STRATEGY_ENGINE_BASE = 'http://strategy-engine:8003';

@Controller('agents')
export class AgentsController {
  private readonly logger = new Logger(AgentsController.name);

  constructor(private readonly httpService: HttpService) {}

  @All('*path')
  async proxy(@Req() req: Request, @Res() res: Response): Promise<void> {
    const path = (req.params as any).path || '';
    const targetUrl = `${STRATEGY_ENGINE_BASE}/agents/${path}`;

    try {
      const response = await firstValueFrom(
        this.httpService.request({
          method: req.method as any,
          url: targetUrl,
          data: req.body,
          params: req.query,
          headers: {
            'content-type': req.headers['content-type'] || 'application/json',
          },
          timeout: 30000,
          validateStatus: () => true,
        }),
      );

      res.status(response.status).json(response.data);
    } catch (err: any) {
      this.logger.error(
        `Strategy engine unreachable at ${targetUrl}: ${err?.message}`,
      );
      res.status(HttpStatus.SERVICE_UNAVAILABLE).json({
        message: 'Agent framework is offline',
        statusCode: HttpStatus.SERVICE_UNAVAILABLE,
      });
    }
  }
}
