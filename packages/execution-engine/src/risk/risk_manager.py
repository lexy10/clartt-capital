"""Risk Manager — validates all risk rules before trade execution."""

import logging
from datetime import datetime, timezone

from src.models import (
    Signal,
    TradingAccount,
    RiskValidationResult,
    RiskRuleResult,
    RiskRuleName,
)

logger = logging.getLogger(__name__)


class RiskManager:
    """Validates trading signals against configurable risk rules.

    Each check method returns True if the check PASSES (no violation).
    validate() runs ALL checks and returns a RiskValidationResult with
    approved=True only if every check passes.
    """

    def __init__(
        self,
        max_risk_per_trade_pct: float = 2.0,
        max_daily_loss_pct: float = 5.0,
        max_positions: int = 10,
        max_lot_exposure: float = 50.0,
        max_spread: float = 5.0,
        max_slippage: float = 3.0,
        max_trailing_drawdown_pct: float = 10.0,
    ) -> None:
        self.max_risk_per_trade_pct = max_risk_per_trade_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_positions = max_positions
        self.max_lot_exposure = max_lot_exposure
        self.max_spread = max_spread
        self.max_slippage = max_slippage
        self.max_trailing_drawdown_pct = max_trailing_drawdown_pct

    def validate(
        self, signal: Signal, account: TradingAccount
    ) -> RiskValidationResult:
        """Run all risk checks and return a consolidated result."""
        rules_checked: list[RiskRuleResult] = []
        first_rejection: RiskRuleName | None = None

        # 1. Max risk per trade
        risk_ok = self.check_max_risk_per_trade(signal, account)
        rules_checked.append(
            RiskRuleResult(
                rule=RiskRuleName.MAX_RISK_PER_TRADE,
                passed=risk_ok,
                reason=None
                if risk_ok
                else (
                    f"Risk per trade exceeds {self.max_risk_per_trade_pct}% of equity"
                ),
            )
        )
        if not risk_ok and first_rejection is None:
            first_rejection = RiskRuleName.MAX_RISK_PER_TRADE

        # 2. Daily loss limit
        daily_ok = self.check_daily_loss_limit(account)
        rules_checked.append(
            RiskRuleResult(
                rule=RiskRuleName.DAILY_LOSS_LIMIT,
                passed=daily_ok,
                reason=None
                if daily_ok
                else (
                    f"Daily loss exceeds {self.max_daily_loss_pct}% of equity"
                ),
            )
        )
        if not daily_ok and first_rejection is None:
            first_rejection = RiskRuleName.DAILY_LOSS_LIMIT

        # 3. Max positions
        pos_ok = self.check_max_positions(account)
        rules_checked.append(
            RiskRuleResult(
                rule=RiskRuleName.MAX_POSITIONS,
                passed=pos_ok,
                reason=None
                if pos_ok
                else f"Open positions at maximum ({self.max_positions})",
            )
        )
        if not pos_ok and first_rejection is None:
            first_rejection = RiskRuleName.MAX_POSITIONS

        # 4. Max lot exposure
        lot_ok = self.check_max_lot_exposure(signal, account)
        rules_checked.append(
            RiskRuleResult(
                rule=RiskRuleName.MAX_LOT_EXPOSURE,
                passed=lot_ok,
                reason=None
                if lot_ok
                else (
                    f"Total lot exposure would exceed {self.max_lot_exposure}"
                ),
            )
        )
        if not lot_ok and first_rejection is None:
            first_rejection = RiskRuleName.MAX_LOT_EXPOSURE

        # 5. Spread check
        current_spread = signal.metadata.spread_at_generation
        spread_ok = self.check_spread(current_spread, self.max_spread)
        rules_checked.append(
            RiskRuleResult(
                rule=RiskRuleName.SPREAD_CHECK,
                passed=spread_ok,
                reason=None
                if spread_ok
                else f"Spread {current_spread} exceeds max {self.max_spread}",
            )
        )
        if not spread_ok and first_rejection is None:
            first_rejection = RiskRuleName.SPREAD_CHECK

        # 6. Slippage check — estimate slippage from volatility ratio
        estimated_slippage = signal.metadata.volatility_ratio
        slip_ok = self.check_slippage(estimated_slippage, self.max_slippage)
        rules_checked.append(
            RiskRuleResult(
                rule=RiskRuleName.SLIPPAGE_CHECK,
                passed=slip_ok,
                reason=None
                if slip_ok
                else (
                    f"Estimated slippage {estimated_slippage} exceeds max {self.max_slippage}"
                ),
            )
        )
        if not slip_ok and first_rejection is None:
            first_rejection = RiskRuleName.SLIPPAGE_CHECK

        # 7. Trailing drawdown check
        dd_ok = self.check_trailing_drawdown(account)
        rules_checked.append(
            RiskRuleResult(
                rule=RiskRuleName.TRAILING_DRAWDOWN,
                passed=dd_ok,
                reason=None
                if dd_ok
                else (
                    f"Trailing drawdown exceeds {self.max_trailing_drawdown_pct}% from HWM"
                ),
            )
        )
        if not dd_ok and first_rejection is None:
            first_rejection = RiskRuleName.TRAILING_DRAWDOWN

        approved = all(r.passed for r in rules_checked)

        if not approved:
            logger.warning(
                "Risk validation REJECTED signal %s for account %s — rule: %s",
                signal.id,
                account.id,
                first_rejection,
            )

        return RiskValidationResult(
            approved=approved,
            signal_id=signal.id,
            account_id=account.id,
            rules_checked=rules_checked,
            rejected_by=first_rejection,
            validated_at=datetime.now(timezone.utc).isoformat(),
        )

    def check_max_risk_per_trade(
        self, signal: Signal, account: TradingAccount
    ) -> bool:
        """Return True if risk per trade is within the allowed percentage of equity."""
        if account.equity <= 0:
            return False
        stop_loss_distance = abs(signal.entry_price - signal.stop_loss)
        risk_amount = stop_loss_distance * signal.position_size
        risk_ratio = risk_amount / account.equity
        return risk_ratio <= self.max_risk_per_trade_pct / 100.0

    def check_daily_loss_limit(self, account: TradingAccount) -> bool:
        """Return True if daily loss is within the allowed percentage of equity."""
        if account.equity <= 0:
            return False
        loss_ratio = account.daily_loss / account.equity
        return loss_ratio <= self.max_daily_loss_pct / 100.0

    def check_max_positions(self, account: TradingAccount) -> bool:
        """Return True if the account has room for another position."""
        return account.open_positions < self.max_positions

    def check_max_lot_exposure(
        self, signal: Signal, account: TradingAccount
    ) -> bool:
        """Return True if adding this signal's lots stays within the limit."""
        return (
            account.total_lot_exposure + signal.position_size
            <= self.max_lot_exposure
        )

    def check_spread(self, current_spread: float, max_spread: float) -> bool:
        """Return True if the current spread is acceptable."""
        return current_spread <= max_spread

    def check_slippage(
        self, estimated_slippage: float, max_slippage: float
    ) -> bool:
        """Return True if the estimated slippage is acceptable."""
        return estimated_slippage <= max_slippage

    def check_trailing_drawdown(self, account: TradingAccount) -> bool:
        """Return True if the account's drawdown from HWM is within the allowed percentage."""
        if account.equity <= 0:
            return False
        # Use the account's high_water_mark if available, otherwise use balance as proxy
        hwm = getattr(account, 'high_water_mark', None) or account.balance
        if hwm <= 0:
            return True
        drawdown_pct = (hwm - account.equity) / hwm * 100
        return drawdown_pct < self.max_trailing_drawdown_pct
