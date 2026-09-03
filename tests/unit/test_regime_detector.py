"""
tests/unit/test_regime_detector.py
Unit tests validating Implied Volatility Rank (IVR), Historical Volatility,
ADX indicators, and Market Regime Classification.
"""

import numpy as np
import pytest

from src.core.event_bus import SignalEvent
from src.data.regime_detector import (
    MarketRegime,
    RegimeClassification,
    RegimeDetector,
    TrendDirection,
)


# ------------------------------------------------------------------------------
# 1. IV Rank & Percentile Tests
# ------------------------------------------------------------------------------

def test_ivr_calculation() -> None:
    """Verifies IV Rank formula: ((Current - Min) / (Max - Min)) * 100."""
    iv_history = [0.15, 0.20, 0.25, 0.30, 0.35]  # Min = 0.15, Max = 0.35, Range = 0.20

    # At midpoint (0.25) -> IVR = 50.0
    assert pytest.approx(RegimeDetector.calculate_ivr(0.25, iv_history), abs=0.01) == 50.0

    # At minimum (0.15) -> IVR = 0.0
    assert pytest.approx(RegimeDetector.calculate_ivr(0.15, iv_history), abs=0.01) == 0.0

    # At maximum (0.35) -> IVR = 100.0
    assert pytest.approx(RegimeDetector.calculate_ivr(0.35, iv_history), abs=0.01) == 100.0

    # Clamped bounds above max or below min
    assert RegimeDetector.calculate_ivr(0.40, iv_history) == 100.0
    assert RegimeDetector.calculate_ivr(0.10, iv_history) == 0.0

    # Flat history edge case (Min == Max)
    assert RegimeDetector.calculate_ivr(0.20, [0.20, 0.20, 0.20]) == 50.0


def test_ivp_calculation() -> None:
    """Verifies IV Percentile: percentage of days below current IV."""
    # 10 values: 0.10, 0.12, ..., 0.28
    iv_history = [0.10, 0.12, 0.14, 0.16, 0.18, 0.20, 0.22, 0.24, 0.26, 0.28]

    # Current IV = 0.17 -> 4 values strictly below (0.10, 0.12, 0.14, 0.16) -> 40%
    assert pytest.approx(RegimeDetector.calculate_ivp(0.17, iv_history), abs=0.01) == 40.0

    # Current IV = 0.05 -> 0 values below -> 0%
    assert RegimeDetector.calculate_ivp(0.05, iv_history) == 0.0

    # Current IV = 0.30 -> all 10 values below -> 100%
    assert RegimeDetector.calculate_ivp(0.30, iv_history) == 100.0


# ------------------------------------------------------------------------------
# 2. Historical Volatility (Close-to-Close & Parkinson) Tests
# ------------------------------------------------------------------------------

def test_close_to_close_and_parkinson_hv() -> None:
    """Verifies that HV metrics produce positive, reasonable annualized volatility figures."""
    np.random.seed(42)
    # Generate synthetic 60-day price path with ~20% annualized vol
    daily_vol = 0.20 / np.sqrt(252)
    returns = np.random.normal(0, daily_vol, 60)
    closes = 500.0 * np.exp(np.cumsum(returns))
    highs = closes * (1 + np.abs(np.random.normal(0, 0.005, 60)))
    lows = closes * (1 - np.abs(np.random.normal(0, 0.005, 60)))

    hv_cc = RegimeDetector.calculate_close_to_close_hv(closes)
    hv_parkinson = RegimeDetector.calculate_parkinson_hv(highs, lows)

    assert 0.05 <= hv_cc <= 0.40
    assert 0.05 <= hv_parkinson <= 0.40


# ------------------------------------------------------------------------------
# 3. ADX & Directional Movement Tests
# ------------------------------------------------------------------------------

def test_adx_trending_vs_rangebound() -> None:
    """Verifies that a strong linear trend produces ADX >= 25, while flat prices produce ADX < 25."""
    # 1. Strong Bullish Trend
    days = 50
    trend_closes = np.linspace(100.0, 150.0, days)
    trend_highs = trend_closes + 1.0
    trend_lows = trend_closes - 0.5

    adx_trend, pdi, mdi = RegimeDetector.calculate_adx(trend_highs, trend_lows, trend_closes, period=14)
    assert adx_trend >= 25.0
    assert pdi > mdi  # Bullish directional dominance

    # 2. Chop / Rangebound Oscillating Prices
    chop_closes = 100.0 + np.sin(np.linspace(0, 10 * np.pi, days)) * 1.5
    chop_highs = chop_closes + 0.5
    chop_lows = chop_closes - 0.5

    adx_chop, _, _ = RegimeDetector.calculate_adx(chop_highs, chop_lows, chop_closes, period=14)
    assert adx_chop < 25.0


# ------------------------------------------------------------------------------
# 4. Market Regime Classification Matrix Tests
# ------------------------------------------------------------------------------

def test_regime_high_iv_rangebound() -> None:
    """
    Scenario 1: High IV (IVR > 50) + Rangebound (ADX < 25)
    Expected: HIGH_IV_RANGEBOUND -> Strategy: Iron Condor
    """
    # 252 days of IV history with IVR ~ 75%
    iv_history = list(np.linspace(0.10, 0.30, 252))
    current_iv = 0.25  # (0.25 - 0.10)/(0.20) = 75% IVR

    # Flat rangebound price series (ADX < 25)
    days = 60
    closes = 500.0 + np.sin(np.linspace(0, 12 * np.pi, days)) * 2.0
    highs = closes + 1.0
    lows = closes - 1.0

    classification = RegimeDetector.classify_regime(
        symbol="SPY",
        current_iv=current_iv,
        iv_history=iv_history,
        high_prices=highs,
        low_prices=lows,
        close_prices=closes,
    )

    assert classification.regime == MarketRegime.HIGH_IV_RANGEBOUND
    assert classification.recommended_strategy == "Iron Condor"
    assert classification.ivr > 50.0
    assert classification.adx < 25.0
    assert classification.confidence >= 0.70


def test_regime_high_iv_trending_bullish() -> None:
    """
    Scenario 2: High IV (IVR > 50) + Strong Bullish Trend (ADX >= 25, +DI > -DI)
    Expected: HIGH_IV_TRENDING -> Strategy: Bull Put Credit Spread
    """
    iv_history = list(np.linspace(0.12, 0.32, 252))
    current_iv = 0.28  # IVR = (0.28 - 0.12) / 0.20 = 80%

    days = 60
    closes = np.linspace(450.0, 520.0, days)  # Strong bull run
    highs = closes + 1.5
    lows = closes - 0.5

    classification = RegimeDetector.classify_regime(
        symbol="QQQ",
        current_iv=current_iv,
        iv_history=iv_history,
        high_prices=highs,
        low_prices=lows,
        close_prices=closes,
    )

    assert classification.regime == MarketRegime.HIGH_IV_TRENDING
    assert classification.recommended_strategy == "Bull Put Credit Spread"
    assert classification.trend_direction == TrendDirection.BULLISH
    assert classification.ivr > 50.0
    assert classification.adx >= 25.0


def test_regime_high_iv_trending_bearish() -> None:
    """
    Scenario 3: High IV (IVR > 50) + Strong Bearish Trend (ADX >= 25, -DI > +DI)
    Expected: HIGH_IV_TRENDING -> Strategy: Bear Call Credit Spread
    """
    iv_history = list(np.linspace(0.15, 0.40, 252))
    current_iv = 0.35  # IVR = 80%

    days = 60
    closes = np.linspace(500.0, 420.0, days)  # Strong bear downtrend
    highs = closes + 0.5
    lows = closes - 1.5

    classification = RegimeDetector.classify_regime(
        symbol="NVDA",
        current_iv=current_iv,
        iv_history=iv_history,
        high_prices=highs,
        low_prices=lows,
        close_prices=closes,
    )

    assert classification.regime == MarketRegime.HIGH_IV_TRENDING
    assert classification.recommended_strategy == "Bear Call Credit Spread"
    assert classification.trend_direction == TrendDirection.BEARISH


def test_regime_low_iv_trending_bullish() -> None:
    """
    Scenario 4: Low IV (IVR <= 50) + Strong Trend (ADX >= 25)
    Expected: LOW_IV_TRENDING -> Strategy: Bull Call Debit Spread
    """
    iv_history = list(np.linspace(0.10, 0.30, 252))
    current_iv = 0.14  # IVR = (0.14 - 0.10) / 0.20 = 20%

    days = 60
    closes = np.linspace(200.0, 240.0, days)  # Strong bull trend
    highs = closes + 1.0
    lows = closes - 0.5

    classification = RegimeDetector.classify_regime(
        symbol="IWM",
        current_iv=current_iv,
        iv_history=iv_history,
        high_prices=highs,
        low_prices=lows,
        close_prices=closes,
    )

    assert classification.regime == MarketRegime.LOW_IV_TRENDING
    assert classification.recommended_strategy == "Bull Call Debit Spread"
    assert classification.ivr <= 50.0
    assert classification.adx >= 25.0


def test_regime_low_iv_chop_cash_preservation() -> None:
    """
    Scenario 5: Low IV (IVR <= 50) + Choppy/No Trend (ADX < 25)
    Expected: LOW_IV_CHOP -> Strategy: Cash Preservation
    """
    iv_history = list(np.linspace(0.10, 0.30, 252))
    current_iv = 0.15  # IVR = 25%

    days = 60
    closes = 220.0 + np.sin(np.linspace(0, 8 * np.pi, days)) * 1.0
    highs = closes + 0.5
    lows = closes - 0.5

    classification = RegimeDetector.classify_regime(
        symbol="IWM",
        current_iv=current_iv,
        iv_history=iv_history,
        high_prices=highs,
        low_prices=lows,
        close_prices=closes,
    )

    assert classification.regime == MarketRegime.LOW_IV_CHOP
    assert classification.recommended_strategy == "Cash Preservation"
    assert classification.ivr <= 50.0
    assert classification.adx < 25.0


def test_regime_classification_to_signal_event() -> None:
    """Verifies that RegimeClassification maps into a valid SignalEvent for the event bus."""
    iv_history = list(np.linspace(0.10, 0.30, 252))
    days = 60
    closes = 500.0 + np.sin(np.linspace(0, 10 * np.pi, days)) * 1.5
    highs = closes + 0.5
    lows = closes - 0.5

    classification = RegimeDetector.classify_regime(
        symbol="SPY",
        current_iv=0.25,
        iv_history=iv_history,
        high_prices=highs,
        low_prices=lows,
        close_prices=closes,
    )

    signal = classification.to_signal_event()
    assert isinstance(signal, SignalEvent)
    assert signal.symbol == "SPY"
    assert signal.regime == "HIGH_IV_RANGEBOUND"
    assert signal.signal_type == "Iron Condor"
    assert "ivr" in signal.metadata
    assert "vol_premium" in signal.metadata
