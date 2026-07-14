import {
  Controller,
  Get,
  Post,
  Put,
  Patch,
  Delete,
  Body,
  Param,
  UseGuards,
  Request,
  ParseUUIDPipe,
  HttpCode,
  HttpStatus,
} from '@nestjs/common';
import { AccountsService } from './accounts.service';
import { InstrumentsService } from '../instruments/instruments.service';
import { JwtAuthGuard } from '../../common/guards/jwt-auth.guard';
import { CreateAccountDto } from './dto/create-account.dto';
import { UpdateLabelDto } from './dto/update-label.dto';
import { UpdateDerivTokenDto } from './dto/update-deriv-token.dto';
import { SetAccountStrategiesDto } from './dto/set-account-strategies.dto';
import { SetAccountInstrumentsDto } from '../instruments/dto/set-account-instruments.dto';

@Controller('accounts')
@UseGuards(JwtAuthGuard)
export class AccountsController {
  constructor(
    private readonly accountsService: AccountsService,
    private readonly instrumentsService: InstrumentsService,
  ) {}

  @Post()
  async create(@Request() req: any, @Body() dto: CreateAccountDto) {
    const account = await this.accountsService.create(req.user.id, dto);
    return AccountsService.sanitize(account);
  }

  @Get()
  async findAll(@Request() req: any) {
    const accounts = await this.accountsService.findAllByUser(req.user.id);
    return accounts.map((a) => AccountsService.sanitize(a));
  }

  @Get(':id/details')
  getDetails(
    @Request() req: any,
    @Param('id', ParseUUIDPipe) id: string,
  ) {
    return this.accountsService.getDetails(req.user.id, id);
  }

  @Get(':id/status')
  getStatus(
    @Request() req: any,
    @Param('id', ParseUUIDPipe) id: string,
  ) {
    return this.accountsService.getStatus(req.user.id, id);
  }

  @Get(':id/broker-symbols')
  getBrokerSymbols(
    @Request() req: any,
    @Param('id', ParseUUIDPipe) id: string,
  ): Promise<string[]> {
    return this.accountsService.getBrokerSymbols(req.user.id, id);
  }


  @Patch(':id/label')
  async updateLabel(
    @Request() req: any,
    @Param('id', ParseUUIDPipe) id: string,
    @Body() dto: UpdateLabelDto,
  ) {
    const account = await this.accountsService.updateLabel(req.user.id, id, dto.label);
    return AccountsService.sanitize(account);
  }

  @Patch(':id/deriv-token')
  async updateDerivToken(
    @Request() req: any,
    @Param('id', ParseUUIDPipe) id: string,
    @Body() dto: UpdateDerivTokenDto,
  ) {
    const account = await this.accountsService.updateDerivToken(
      req.user.id, id, dto.derivApiToken, dto.derivLoginId,
    );
    return AccountsService.sanitize(account);
  }

  @Post(':id/deploy')
  @HttpCode(HttpStatus.NO_CONTENT)
  deploy(
    @Request() req: any,
    @Param('id', ParseUUIDPipe) id: string,
  ) {
    return this.accountsService.deploy(req.user.id, id);
  }

  @Post(':id/undeploy')
  @HttpCode(HttpStatus.NO_CONTENT)
  undeploy(
    @Request() req: any,
    @Param('id', ParseUUIDPipe) id: string,
  ) {
    return this.accountsService.undeploy(req.user.id, id);
  }

  @Post(':id/reconcile')
  reconcile(
    @Request() req: any,
    @Param('id', ParseUUIDPipe) id: string,
    @Body() body: { lookback_hours?: number; refresh_closed_hours?: number } = {},
  ) {
    return this.accountsService.reconcile(
      req.user.id,
      id,
      body.lookback_hours ?? 168,
      body.refresh_closed_hours ?? 0,
    );
  }

  @Delete(':id')
  @HttpCode(HttpStatus.NO_CONTENT)
  remove(
    @Request() req: any,
    @Param('id', ParseUUIDPipe) id: string,
  ) {
    return this.accountsService.remove(req.user.id, id);
  }

  @Get(':id/strategies')
  getAccountStrategies(
    @Request() req: any,
    @Param('id', ParseUUIDPipe) id: string,
  ) {
    return this.accountsService.getAccountStrategies(req.user.id, id);
  }

  @Put(':id/strategies')
  setAccountStrategies(
    @Request() req: any,
    @Param('id', ParseUUIDPipe) id: string,
    @Body() dto: SetAccountStrategiesDto,
  ) {
    return this.accountsService.setAccountStrategies(req.user.id, id, dto.strategyIds);
  }

  @Get(':id/instruments')
  getAccountInstruments(
    @Param('id', ParseUUIDPipe) id: string,
  ) {
    return this.instrumentsService.getAccountInstruments(id);
  }

  @Put(':id/instruments')
  setAccountInstruments(
    @Request() req: any,
    @Param('id', ParseUUIDPipe) id: string,
    @Body() dto: SetAccountInstrumentsDto,
  ) {
    return this.accountsService.setAccountInstruments(req.user.id, id, dto.instruments);
  }
}
