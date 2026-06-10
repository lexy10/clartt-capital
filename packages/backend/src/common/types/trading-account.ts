export interface TradingAccount {
  id: string;                    // UUID
  user_id: string;
  metaapi_account_id: string;    // MetaApi provisioned account ID
  label?: string;
  is_active: boolean;
  equity: number;
  balance: number;
  open_positions: number;
  total_lot_exposure: number;
  daily_loss: number;
  created_at: string;            // ISO 8601
}
