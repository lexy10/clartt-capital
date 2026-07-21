"""Deriv multiplier selection + stop-loss clamping.

Deriv multiplier contracts can never lose more than their stake, so the broker
rejects any stop_loss whose USD value exceeds the stake ("Enter an amount equal
to or lower than <stake>"). These tests lock in the fix: the multiplier is chosen
so the stop fits, and the stop is clamped to the stake as a last resort.
"""

import pytest

from src.executor.clients.deriv import DerivSyntheticClient


@pytest.fixture
def client() -> DerivSyntheticClient:
    return DerivSyntheticClient(app_id="1089", api_token="test-token")


def test_select_multiplier_fits_stop_within_stake(client: DerivSyntheticClient):
    # 0.5% stop on R_25: 400x would need 2x the stake (rejected). The largest
    # allowed multiplier keeping the stop inside the stake is 160.
    price = 2551.0
    sl = price + price * 0.005  # SELL stop 0.5% away
    mult = client._select_multiplier("R_25", price, sl)
    assert mult == 160
    # Sanity: at this multiplier the stop is within the stake.
    assert mult * (abs(price - sl) / price) <= 1.0


def test_select_multiplier_wide_stop_falls_back_to_smallest(client: DerivSyntheticClient):
    # A very wide stop (5%) can't fit even the smallest R_25 multiplier (160):
    # 160 * 0.05 = 8x the stake. We fall back to the smallest allowed.
    price = 2551.0
    sl = price + price * 0.05
    mult = client._select_multiplier("R_25", price, sl)
    assert mult == 160  # smallest allowed for R_25


def test_select_multiplier_no_sl_uses_smallest(client: DerivSyntheticClient):
    assert client._select_multiplier("R_25", 2551.0, 0.0) == 160


def _capture_buy_params(client: DerivSyntheticClient, monkeypatch):
    """Patch the network layer so send_order builds params without a socket."""
    captured: dict = {}

    async def fake_ensure_connected(token=None):
        return None

    async def fake_send_and_wait(payload, timeout=30):
        captured["payload"] = payload
        return {"buy": {"contract_id": 123}}

    monkeypatch.setattr(client, "_ensure_connected", fake_ensure_connected)
    monkeypatch.setattr(client, "_send_and_wait", fake_send_and_wait)
    return captured


def test_send_order_stop_loss_within_stake(client: DerivSyntheticClient, monkeypatch):
    captured = _capture_buy_params(client, monkeypatch)
    price = 2551.0
    sl = price + price * 0.005  # SELL
    res = client.send_order("R_25", "SELL", volume=1.0, price=price, sl=sl, tp=price - price * 0.01)
    assert res.success
    limit = captured["payload"]["parameters"]["limit_order"]
    stake = captured["payload"]["parameters"]["amount"]
    # The key invariant: broker stop-loss never exceeds the stake.
    assert limit["stop_loss"] <= stake


def test_send_order_wide_stop_is_clamped(client: DerivSyntheticClient, monkeypatch):
    captured = _capture_buy_params(client, monkeypatch)
    price = 2551.0
    sl = price + price * 0.05  # 5% — wider than any multiplier can honor
    res = client.send_order("R_25", "SELL", volume=1.0, price=price, sl=sl, tp=price - price * 0.1)
    assert res.success
    limit = captured["payload"]["parameters"]["limit_order"]
    stake = captured["payload"]["parameters"]["amount"]
    # Clamped to the safety fraction of the stake, so Deriv accepts it.
    assert limit["stop_loss"] <= stake
    assert limit["stop_loss"] == pytest.approx(round(stake * client.SL_STAKE_SAFETY, 2))
