import {
  Controller,
  Post,
  Body,
  HttpCode,
  HttpStatus,
  Req,
} from '@nestjs/common';
import { Request } from 'express';
import { Throttle } from '@nestjs/throttler';
import { AuthService } from './auth.service';
import { LoginDto } from './dto/login.dto';
import { RefreshDto } from './dto/refresh.dto';

@Controller('auth')
export class AuthController {
  constructor(private readonly authService: AuthService) {}

  // Brute-force guard: 5 attempts/min per IP, on top of bcrypt's cost.
  // A legitimate user mistyping a password a few times stays well inside
  // this; a credential-stuffing script does not.
  @Throttle({ default: { ttl: 60_000, limit: 5 } })
  @Post('login')
  @HttpCode(HttpStatus.OK)
  async login(@Body() dto: LoginDto, @Req() req: Request) {
    return this.authService.login(dto.email, dto.password, req.ip);
  }

  // Refresh fires automatically on 401s — allow more than login, but still
  // tight enough to blunt token-guessing.
  @Throttle({ default: { ttl: 60_000, limit: 30 } })
  @Post('refresh')
  @HttpCode(HttpStatus.OK)
  async refresh(@Body() dto: RefreshDto, @Req() req: Request) {
    return this.authService.refresh(dto.refresh_token, req.ip);
  }

  @Post('logout')
  @HttpCode(HttpStatus.OK)
  async logout(@Body() dto: RefreshDto, @Req() req: Request) {
    const userId = (req as any).user?.id;
    await this.authService.logout(dto.refresh_token, userId, req.ip);
    return { message: 'Logged out' };
  }
}
