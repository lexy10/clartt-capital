"""Unit tests for the RiskManager."""

import pytest

from src.models import (
    Signal,
    SignalDirection,
    SignalMetadata,
    SignalMode,
    BOSType,
    Timeframe,
    TradingAccount,
    RiskRuleName,
)
from src.risk.risk_manager import RiskManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(
    entry_price: float = 34500.0,
    stop_loss: float = 34450.0,
    position_size: float = 0.1,
    spread: float = 2.5,
    volatility_ratio: float = 1.2,
) -> Signal:
    return Signal(
        id="sig-001",
        instrument="US30",
        direction=SignalDirection.BUY,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=entry_price + 100,
        position_size=position_size,
        confidence_score=0.85,
        timeframe=Timeframe.FIFTEEN_MINUTES,
        order_block_id="ob-001",
        strategy_id="strat-001",
        mode=SignalMode.LIVE,
        metadata=SignalMetadata(
            bos_type=BOSType.BULLISH,
            liquidity_swept=True,
            session="new_york",
            spread_at_generation=spread,
            volatility_ratio=volatility_ratio,
        ),
        created_at="2024-01-15T10:30:00Z",
    )


def _make_account(
    equity: float = 10000.0,
    daily_loss: float = 0.0,
    open_positions: int = 2,
    total_lot_exposure: float = 0.5,
) -> TradingAccount:
    return TradingAccount(
        id="acc-001",
        user_id="user-001",
        metaapi_account_id="test-account-id",
        label="Demo",
        is_active=True,
        equity=equity,
        balance=equity,
        open_positions=open_positions,
        daily_loss=daily_loss,
        total_lot_exposure=total_lot_exposure,
    )


# ---------------------------------------------------------------------------
# validate() — full pipeline
# ---------------------------------------------------------------------------

class TestValidate:
    def test_all_checks_pass(self):
        rm = RiskManager()
        sig = _make_signal()
        acc = _make_account()
        result = rm.validate(sig, acc)

        assert result.approved is True
        assert result.rejected_by is None
        assert result.signal_id == sig.id
        assert result.account_id == acc.id
        assert len(result.rules_checked) == 7
        assert all(r.passed for r in result.rules_checked)

    def test_single_failure_rejects(self):
        rm = RiskManager(max_positions=2)
        sig = _make_signal()
        acc = _make_account(open_positions=2)  # at max
        result = rm.validate(sig, acc)

        assert result.approved is False
        assert result.rejected_by == RiskRuleName.MAX_POSITIONS

    def test_multiple_failures_reports_first(self):
        rm = RiskManager(max_positions=1, max_daily_loss_pct=0.01)
        sig = _make_signal()
        acc = _make_account(open_positions=5, daily_loss=500.0)
        result = rm.validate(sig, acc)

        assert result.approved is False
        # daily loss check comes before max positions in the pipeline
        assert result.rejected_by == RiskRuleName.DAILY_LOSS_LIMIT
        failed = [r for r in result.rules_checked if not r.passed]
        assert len(failed) >= 2

    def test_all_seven_rules_are_checked(self):
        rm = RiskManager()
        sig = _make_signal()
        acc = _make_account()
        result = rm.validate(sig, acc)

        rule_names = {r.rule for r in result.rules_checked}
        expected = {
            RiskRuleName.MAX_RISK_PER_TRADE,
            RiskRuleName.DAILY_LOSS_LIMIT,
            RiskRuleName.MAX_POSITIONS,
            RiskRuleName.MAX_LOT_EXPOSURE,
            RiskRuleName.SPREAD_CHECK,
            RiskRuleName.SLIPPAGE_CHECK,
            RiskRuleName.TRAILING_DRAWDOWN,
        }
        assert rule_names == expected


# ---------------------------------------------------------------------------
# check_max_risk_per_trade
# ---------------------------------------------------------------------------

class TestMaxRiskPerTrade:
    def test_within_limit(self):
        rm = RiskManager(max_risk_per_trade_pct=2.0)
        # risk = |34500 - 34450| * 0.1 = 5.0, ratio = 5/10000 = 0.05%
        sig = _make_signal(entry_price=34500.0, stop_loss=34450.0, position_size=0.1)
        acc = _make_account(equity=10000.0)
        assert rm.check_max_risk_per_trade(sig, acc) is True

    def test_exceeds_limit(self):
        rm = RiskManager(max_risk_per_trade_pct=1.0)
        # risk = |34500 - 34000| * 1.0 = 500, ratio = 500/10000 = 5%
        sig = _make_signal(entry_price=34500.0, stop_loss=34000.0, position_size=1.0)
        acc = _make_account(equity=10000.0)
        assert rm.check_max_risk_per_trade(sig, acc) is False

    def test_exactly_at_limit(self):
        rm = RiskManager(max_risk_per_trade_pct=2.0)
        # risk = 200 * 1.0 = 200, ratio = 200/10000 = 2.0% == limit
        sig = _make_signal(entry_price=34500.0, stop_loss=34300.0, position_size=1.0)
        acc = _make_account(equity=10000.0)
        assert rm.check_max_risk_per_trade(sig, acc) is True

    def test_zero_equity_fails(self):
        rm = RiskManager()
        sig = _make_signal()
        acc = _make_account(equity=0.0)
        assert rm.check_max_risk_per_trade(sig, acc) is False


# ---------------------------------------------------------------------------
# check_daily_loss_limit
# ---------------------------------------------------------------------------

class TestDailyLossLimit:
    def test_within_limit(self):
        rm = RiskManager(max_daily_loss_pct=5.0)
        acc = _make_account(equity=10000.0, daily_loss=100.0)  # 1%
        assert rm.check_daily_loss_limit(acc) is True

    def test_exceeds_limit(self):
        rm = RiskManager(max_daily_loss_pct=5.0)
        acc = _make_account(equity=10000.0, daily_loss=600.0)  # 6%
        assert rm.check_daily_loss_limit(acc) is False

    def test_exactly_at_limit(self):
        rm = RiskManager(max_daily_loss_pct=5.0)
        acc = _make_account(equity=10000.0, daily_loss=500.0)  # 5%
        assert rm.check_daily_loss_limit(acc) is True

    def test_zero_equity_fails(self):
        rm = RiskManager()
        acc = _make_account(equity=0.0, daily_loss=0.0)
        assert rm.check_daily_loss_limit(acc) is False


# ---------------------------------------------------------------------------
# check_max_positions
# ---------------------------------------------------------------------------

class TestMaxPositions:
    def test_below_limit(self):
        rm = RiskManager(max_positions=10)
        acc = _make_account(open_positions=5)
        assert rm.check_max_positions(acc) is True

    def test_at_limit(self):
        rm = RiskManager(max_positions=10)
        acc = _make_account(open_positions=10)
        assert rm.check_max_positions(acc) is False

    def test_above_limit(self):
        rm = RiskManager(max_positions=10)
        acc = _make_account(open_positions=15)
        assert rm.check_max_positions(acc) is False

    def test_zero_positions(self):
        rm = RiskManager(max_positions=10)
        acc = _make_account(open_positions=0)
        assert rm.check_max_positions(acc) is True


# ---------------------------------------------------------------------------
# check_max_lot_exposure
# ---------------------------------------------------------------------------

class TestMaxLotExposure:
    def test_within_limit(self):
        rm = RiskManager(max_lot_exposure=50.0)
        sig = _make_signal(position_size=1.0)
        acc = _make_account(total_lot_exposure=10.0)
        assert rm.check_max_lot_exposure(sig, acc) is True

    def test_exceeds_limit(self):
        rm = RiskManager(max_lot_exposure=50.0)
        sig = _make_signal(position_size=5.0)
        acc = _make_account(total_lot_exposure=48.0)
        assert rm.check_max_lot_exposure(sig, acc) is False

    def test_exactly_at_limit(self):
        rm = RiskManager(max_lot_exposure=50.0)
        sig = _make_signal(position_size=2.0)
        acc = _make_account(total_lot_exposure=48.0)
        assert rm.check_max_lot_exposure(sig, acc) is True


# ---------------------------------------------------------------------------
# check_spread
# ---------------------------------------------------------------------------

class TestSpreadCheck:
    def test_within_limit(self):
        rm = RiskManager()
        assert rm.check_spread(2.0, 5.0) is True

    def test_exceeds_limit(self):
        rm = RiskManager()
        assert rm.check_spread(6.0, 5.0) is False

    def test_exactly_at_limit(self):
        rm = RiskManager()
        assert rm.check_spread(5.0, 5.0) is True


# ---------------------------------------------------------------------------
# check_slippage
# ---------------------------------------------------------------------------

class TestSlippageCheck:
    def test_within_limit(self):
        rm = RiskManager()
        assert rm.check_slippage(1.0, 3.0) is True

    def test_exceeds_limit(self):
        rm = RiskManager()
        assert rm.check_slippage(4.0, 3.0) is False

    def test_exactly_at_limit(self):
        rm = RiskManager()
        assert rm.check_slippage(3.0, 3.0) is True


# ---------------------------------------------------------------------------
# Integration: validate uses spread/slippage from signal metadata
# ---------------------------------------------------------------------------

class TestValidateUsesSignalMetadata:
    def test_high_spread_in_metadata_rejects(self):
        rm = RiskManager(max_spread=3.0)
        sig = _make_signal(spread=5.0)
        acc = _make_account()
        result = rm.validate(sig, acc)

        assert result.approved is False
        spread_rule = next(
            r for r in result.rules_checked if r.rule == RiskRuleName.SPREAD_CHECK
        )
        assert spread_rule.passed is False

    def test_high_volatility_ratio_as_slippage_rejects(self):
        rm = RiskManager(max_slippage=1.0)
        sig = _make_signal(volatility_ratio=2.0)
        acc = _make_account()
        result = rm.validate(sig, acc)

        assert result.approved is False
        slip_rule = next(
            r for r in result.rules_checked if r.rule == RiskRuleName.SLIPPAGE_CHECK
        )
        assert slip_rule.passed is False
