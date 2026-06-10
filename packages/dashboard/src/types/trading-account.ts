export interface TradingAccount {
  id: string;
  userId: string;
  metaapiAccountId: string;
  label?: string;
  isActive: boolean;
  mt5Login?: string;
  mt5Server?: string;
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
  login: string;
  password: string;
  serverName: string;
  platform: 'mt5' | 'mt4';
  label?: string;
}
