export type BrokerProvider = 'metaapi' | 'deriv' | 'alpaca' | 'binance' | 'ibkr' | 'stub';
export type AccountKind = 'personal' | 'prop' | 'demo';

export interface TradingAccount {
  id: string;
  userId: string;
  metaapiAccountId: string | null;
  label?: string;
  isActive: boolean;
  brokerProvider?: BrokerProvider | null;
  accountKind?: AccountKind;
  // MetaAPI / MT5
  mt5Login?: string;
  mt5Server?: string;
  // Deriv (token is never sent to the client)
  derivLoginId?: string | null;
  createdAt: string;
}

export interface AccountDetails {
  balance: number;
  equity: number;
  margin: number;
  free_margin: number;
  open_positions: number;
  leverage: number;
  state?: string;           // MetaAPI state: DEPLOYED, UNDEPLOYED, DEPLOYING, etc.
  connection_status?: string; // CONNECTED, DISCONNECTED, TIMEOUT, etc.
}

export interface CreateAccountDto {
  brokerProvider?: 'metaapi' | 'deriv';
  accountKind?: AccountKind;
  label?: string;
  // MetaAPI / MT5 flow
  login?: string;
  password?: string;
  serverName?: string;
  platform?: 'mt5' | 'mt4';
  // Deriv-direct flow
  derivApiToken?: string;
  derivLoginId?: string;
}
