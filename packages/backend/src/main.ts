import { NestFactory } from '@nestjs/core';
import { ValidationPipe } from '@nestjs/common';
import helmet from 'helmet';
import { AppModule } from './app.module';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);

  // Trust the reverse-proxy chain (Caddy → dashboard nginx → backend) so
  // req.ip reflects the REAL client IP from X-Forwarded-For, not the nginx
  // container's IP. Without this, every request appears to come from one IP
  // and the rate limiter throttles all users collectively (which was silently
  // 429-ing the dashboard's polling). Only private/loopback hops are trusted,
  // so an external client can't spoof X-Forwarded-For to dodge the limiter or
  // poison audit logs.
  app.getHttpAdapter().getInstance().set('trust proxy', 'loopback, uniquelocal');

  app.setGlobalPrefix('api');

  // Security headers. CSP is off here because the API serves JSON, not
  // HTML — the dashboard's CSP lives in its nginx config. The rest
  // (nosniff, frame denial, HSTS when behind TLS) applies as-is.
  app.use(helmet({ contentSecurityPolicy: false }));

  app.enableCors({
    origin: process.env.CORS_ORIGIN || 'http://localhost:5173',
    credentials: true,
  });

  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      forbidNonWhitelisted: true,
      transform: true,
      transformOptions: {
        enableImplicitConversion: true,
      },
    }),
  );

  const port = process.env.PORT || 3000;
  await app.listen(port);
  console.log(`Backend listening on port ${port}`);
}

bootstrap();
