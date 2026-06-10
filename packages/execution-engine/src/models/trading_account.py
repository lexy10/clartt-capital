from typing import Optional

from pydantic import BaseModel


class TradingAccount(BaseModel):
    id: str  # UUID
    user_id: str  # UUID
    metaapi_account_id: Optional[str] = None  # MetaAPI account ID (None for Deriv-direct accounts)
    label: Optional[str] = None
    is_active: bool = True

    # --- Routing & funding ---
    account_kind: str = "personal"          # 'personal' | 'prop' | 'demo'
    broker_provider: Optional[str] = None   # Explicit override: 'deriv'|'metaapi'|... or None to auto-route

    # --- Deriv-direct credentials (only set for brokerProvider='deriv') ---
    deriv_api_token: Optional[str] = None
    deriv_login_id: Optional[str] = None

    # --- Prop firm settings (null on personal/demo) ---
    prop_firm_name: Optional[str] = None
    prop_max_daily_loss_pct: Optional[float] = None      # e.g. 5.0 for 5%
    prop_max_total_drawdown_pct: Optional[float] = None  # e.g. 10.0 for 10%
    prop_profit_target_pct: Optional[float] = None       # e.g. 8.0 for 8%

    # --- Runtime state (not persisted) ---
    equity: float = 0.0
    balance: float = 0.0
    open_positions: int = 0
    daily_loss: float = 0.0
    total_lot_exposure: float = 0.0
