"""
tests/unit/test_order_builder.py
Unit tests for OCC 21-character symbology formatting, multi-leg spread constructors,
and Multi-Model StrategistAgent gateway.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent
from alpaca.trading.requests import LimitOrderRequest

from src.agents.strategist_agent import StrategistAgent
from src.core.exceptions import OCCFormattingError
from src.data.regime_detector import MarketRegime, RegimeClassification, TrendDirection
from src.execution.order_builder import (
    build_bear_call_spread,
    build_bull_put_spread,
    build_iron_condor,
    format_occ_symbol,
    to_alpaca_symbol,
)
from src.risk.hard_gates import TradeProposal


# ------------------------------------------------------------------------------
# 1. 21-Character OCC Format & Symbology Tests
# ------------------------------------------------------------------------------

def test_occ_symbol_exact_21_char_length() -> None:
    """Verifies that padded=True produces an exact 21-character standard OCC symbol."""
    test_cases = [
        # (Root, Exp Date, Option Type, Strike, Expected Padded, Expected Unpadded)
        ("SPY", "2026-09-18", "call", 560.0, "SPY   260918C00560000", "SPY260918C00560000"),
        ("QQQ", date(2026, 10, 16), "put", 480.0, "QQQ   261016P00480000", "QQQ261016P00480000"),
        ("IWM", "2026-11-20", "P", 215.0, "IWM   261120P00215000", "IWM261120P00215000"),
        ("NVDA", date(2026, 12, 18), "C", 125.50, "NVDA  261218C00125500", "NVDA261218C00125500"),
        ("AAPL", "2026-08-21", "put", 220.25, "AAPL  260821P00220250", "AAPL260821P00220250"),
        ("MSFT", "2026-09-04", "call", 450.0, "MSFT  260904C00450000", "MSFT260904C00450000"),
    ]

    for root, exp, opt_type, strike, exp_padded, exp_unpadded in test_cases:
        padded_sym = format_occ_symbol(root, exp, opt_type, strike, padded=True)
        unpadded_sym = format_occ_symbol(root, exp, opt_type, strike, padded=False)

        assert len(padded_sym) == 21, f"Failed 21-character length check for {root}"
        assert padded_sym == exp_padded
        assert unpadded_sym == exp_unpadded
        assert to_alpaca_symbol(padded_sym) == exp_unpadded


def test_occ_symbol_formatting_exceptions() -> None:
    """Verifies that invalid root tickers, dates, types, or strikes raise OCCFormattingError."""
    with pytest.raises(OCCFormattingError):
        # Ticker longer than 6 characters
        format_occ_symbol("TOOLONGTICKER", "2026-09-18", "C", 100.0)

    with pytest.raises(OCCFormattingError):
        # Invalid strike price <= 0
        format_occ_symbol("SPY", "2026-09-18", "C", 0.0)

    with pytest.raises(OCCFormattingError):
        # Invalid option type
        format_occ_symbol("SPY", "2026-09-18", "INVALID", 100.0)


# ------------------------------------------------------------------------------
# 2. Multi-Leg Defined-Risk Spread Constructor Tests
# ------------------------------------------------------------------------------

def test_build_bull_put_spread_valid() -> None:
    """Verifies valid Bull Put spread construction with Alpaca LimitOrderRequest and TradeProposal."""
    order_req, proposal = build_bull_put_spread(
        underlying="SPY",
        expiration="2026-09-18",
        short_strike=550.0,
        long_strike=540.0,
        credit=1.20,
        quantity=2,
        dte=30,
    )

    assert isinstance(order_req, LimitOrderRequest)
    assert order_req.order_class == OrderClass.MLEG
    assert order_req.qty == 2.0
    assert order_req.limit_price == 1.20
    assert len(order_req.legs) == 2

    # Leg 1: Short Put (Sell to Open)
    assert order_req.legs[0].side == OrderSide.SELL
    assert order_req.legs[0].position_intent == PositionIntent.SELL_TO_OPEN
    assert "P00550000" in order_req.legs[0].symbol

    # Leg 2: Long Put (Buy to Open)
    assert order_req.legs[1].side == OrderSide.BUY
    assert order_req.legs[1].position_intent == PositionIntent.BUY_TO_OPEN
    assert "P00540000" in order_req.legs[1].symbol

    # Proposal math: width = $10, credit = $1.20 -> max loss = ($10 - $1.20) * 100 = $880
    assert isinstance(proposal, TradeProposal)
    assert proposal.symbol == "SPY"
    assert proposal.max_loss_per_contract == 880.0
    assert proposal.required_margin_per_contract == 1000.0
    assert proposal.target_credit_per_contract == 120.0


def test_build_bull_put_spread_inverted_strikes_rejected() -> None:
    """Inverted strikes (short_strike <= long_strike) must raise ValueError."""
    with pytest.raises(ValueError, match="short_strike .* > long_strike"):
        build_bull_put_spread(
            underlying="SPY",
            expiration="2026-09-18",
            short_strike=540.0,
            long_strike=550.0,
            credit=1.20,
        )


def test_build_bear_call_spread_valid() -> None:
    """Verifies Bear Call spread construction: Sell lower Call, Buy higher Call."""
    order_req, proposal = build_bear_call_spread(
        underlying="QQQ",
        expiration="2026-10-16",
        short_strike=480.0,
        long_strike=490.0,
        credit=1.50,
        quantity=1,
        dte=25,
    )

    assert order_req.order_class == OrderClass.MLEG
    assert len(order_req.legs) == 2
    assert order_req.legs[0].side == OrderSide.SELL
    assert order_req.legs[1].side == OrderSide.BUY
    assert proposal.max_loss_per_contract == 850.0  # (10 - 1.50) * 100
    assert proposal.required_margin_per_contract == 1000.0


def test_build_bear_call_spread_inverted_strikes_rejected() -> None:
    """Inverted strikes (short_strike >= long_strike) must raise ValueError."""
    with pytest.raises(ValueError, match="short_strike .* < long_strike"):
        build_bear_call_spread(
            underlying="QQQ",
            expiration="2026-10-16",
            short_strike=500.0,
            long_strike=490.0,
            credit=1.50,
        )


def test_build_iron_condor_valid_and_invalid_hierarchies() -> None:
    """Verifies 4-leg Iron Condor construction and strike hierarchy enforcement."""
    # Valid Iron Condor: 520 Put Buy, 530 Put Sell, 560 Call Sell, 570 Call Buy
    order_req, proposal = build_iron_condor(
        underlying="SPY",
        expiration="2026-09-18",
        put_long_strike=520.0,
        put_short_strike=530.0,
        call_short_strike=560.0,
        call_long_strike=570.0,
        net_credit=2.20,
        quantity=1,
        dte=35,
    )

    assert len(order_req.legs) == 4
    assert order_req.limit_price == 2.20
    assert proposal.strategy_name == "Iron Condor"
    assert proposal.max_loss_per_contract == 780.0  # (10 - 2.20) * 100
    assert proposal.required_margin_per_contract == 1000.0  # Margin held on one wing

    # Invalid: Call short <= Put short
    with pytest.raises(ValueError, match="Iron Condor strikes must satisfy"):
        build_iron_condor(
            underlying="SPY",
            expiration="2026-09-18",
            put_long_strike=520.0,
            put_short_strike=550.0,
            call_short_strike=540.0,  # Invalid: Call short < Put short
            call_long_strike=560.0,
            net_credit=2.0,
        )


# ------------------------------------------------------------------------------
# 3. Multi-Model StrategistAgent Tests
# ------------------------------------------------------------------------------

@pytest.fixture
def mock_regime_high_iv_rangebound() -> RegimeClassification:
    """Synthetic HIGH_IV_RANGEBOUND classification fixture."""
    return RegimeClassification(
        symbol="SPY",
        regime=MarketRegime.HIGH_IV_RANGEBOUND,
        recommended_strategy="Iron Condor",
        trend_direction=TrendDirection.NEUTRAL,
        confidence=0.85,
        current_iv=0.28,
        ivr=75.0,
        ivp=80.0,
        historical_vol_cc=0.18,
        historical_vol_parkinson=0.17,
        vol_premium=0.10,
        adx=18.0,
        plus_di=20.0,
        minus_di=19.0,
        ema_20=500.0,
        ema_50=495.0,
        ema_200=480.0,
    )


@pytest.fixture
def mock_regime_low_iv_chop() -> RegimeClassification:
    """Synthetic LOW_IV_CHOP classification fixture."""
    return RegimeClassification(
        symbol="IWM",
        regime=MarketRegime.LOW_IV_CHOP,
        recommended_strategy="Cash Preservation",
        trend_direction=TrendDirection.NEUTRAL,
        confidence=0.80,
        current_iv=0.14,
        ivr=20.0,
        ivp=18.0,
        historical_vol_cc=0.15,
        historical_vol_parkinson=0.14,
        vol_premium=-0.01,
        adx=14.0,
        plus_di=15.0,
        minus_di=16.0,
        ema_20=210.0,
        ema_50=210.0,
        ema_200=208.0,
    )


@pytest.mark.asyncio
async def test_strategist_agent_deterministic_fallback(
    mock_regime_high_iv_rangebound: RegimeClassification,
) -> None:
    """Verifies that StrategistAgent produces a valid TradeProposal matching the regime in mock mode."""
    agent = StrategistAgent(mock_mode=True)
    await agent.start()

    proposal = await agent.formulate_strategy(
        underlying="SPY",
        current_price=550.0,
        regime=mock_regime_high_iv_rangebound,
    )

    assert proposal is not None
    assert proposal.symbol == "SPY"
    assert proposal.strategy_name == "Iron Condor"
    assert proposal.ivr == 75.0
    assert proposal.max_loss_per_contract == 350.0
    assert agent.telemetry.proposals_generated == 1
    await agent.stop()


@pytest.mark.asyncio
async def test_strategist_agent_low_iv_chop_preserves_cash(
    mock_regime_low_iv_chop: RegimeClassification,
) -> None:
    """Verifies that in a LOW_IV_CHOP regime, strategist preserves cash and returns None."""
    agent = StrategistAgent(mock_mode=True)
    proposal = await agent.formulate_strategy(
        underlying="IWM",
        current_price=210.0,
        regime=mock_regime_low_iv_chop,
    )
    assert proposal is None


@pytest.mark.asyncio
async def test_strategist_featherless_retry_on_503(
    mock_regime_high_iv_rangebound: RegimeClassification,
) -> None:
    """Verifies automatic 3-attempt exponential backoff retry on Featherless HTTP 503 capacity errors."""
    agent = StrategistAgent(mock_mode=False, provider="featherless")

    # Mock AsyncOpenAI client
    mock_client = MagicMock()
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()

    # Fail twice with 503 capacity error, succeed on 3rd attempt
    err_503 = Exception("HTTP 503 Service Unavailable: capacity limit exceeded")
    success_response = MagicMock()
    success_response.choices = [
        MagicMock(
            message=MagicMock(
                content="""{
  "strategy_name": "Iron Condor",
  "thesis": "Featherless model edge",
  "dte": 30,
  "target_credit": 1.50,
  "max_loss": 350.0,
  "quantity": 1
}"""
            )
        )
    ]

    mock_client.chat.completions.create = AsyncMock(
        side_effect=[err_503, err_503, success_response]
    )
    agent._featherless_client = mock_client

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        proposal = await agent.formulate_strategy(
            underlying="SPY",
            current_price=550.0,
            regime=mock_regime_high_iv_rangebound,
        )

        assert proposal is not None
        assert proposal.strategy_name == "Iron Condor"
        assert proposal.thesis == "Featherless model edge"
        assert mock_client.chat.completions.create.call_count == 3
        # Assert backoff delays called: 1.0s, 2.0s
        assert mock_sleep.call_count == 2
