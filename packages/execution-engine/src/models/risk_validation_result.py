from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


class RiskRuleName(str, Enum):
    MAX_RISK_PER_TRADE = "max_risk_per_trade"
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    MAX_POSITIONS = "max_positions"
    MAX_LOT_EXPOSURE = "max_lot_exposure"
    SPREAD_CHECK = "spread_check"
    SLIPPAGE_CHECK = "slippage_check"
    TRAILING_DRAWDOWN = "trailing_drawdown"


class RiskRuleResult(BaseModel):
    rule: RiskRuleName
    passed: bool
    reason: Optional[str] = None


class RiskValidationResult(BaseModel):
    approved: bool
    signal_id: str
    account_id: str
    rules_checked: List[RiskRuleResult]
    rejected_by: Optional[RiskRuleName] = None
    validated_at: str  # ISO 8601
