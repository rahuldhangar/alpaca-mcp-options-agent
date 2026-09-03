"""
src/data/regime_detector.py
Quantitative market regime classification and volatility edge detection.

Calculates:
1. Implied Volatility Rank (IVR, 252 trading days)
2. Implied Volatility Percentile (IVP, 252 trading days)
3. Historical Volatility (Close-to-Close and Parkinson High-Low)
4. Volatility Premium (IV - HV)
5. Average Directional Index (ADX, 14-period) & Directional Indicators (+DI, -DI)
6. EMA Trend Hierarchy (20 / 50 / 200 period moving averages)

Regime Decision Matrix:
- HIGH_IV_RANGEBOUND: IVR > 50, ADX < 25 -> Iron Condor / Delta-neutral premium collection
- HIGH_IV_TRENDING:   IVR > 50, ADX >= 25 -> Bull Put or Bear Call Directional Credit Spreads
- LOW_IV_TRENDING:    IVR <= 50, ADX >= 25 -> Directional Vertical Debit Spreads
- LOW_IV_CHOP:        IVR <= 50, ADX < 25 -> Cash Preservation / No Trade
"""

from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from src.core.event_bus import SignalEvent


class MarketRegime(str, Enum):
    """Enumeration of active market volatility and trend regimes."""

    HIGH_IV_RANGEBOUND = "HIGH_IV_RANGEBOUND"
    HIGH_IV_TRENDING = "HIGH_IV_TRENDING"
    LOW_IV_TRENDING = "LOW_IV_TRENDING"
    LOW_IV_CHOP = "LOW_IV_CHOP"


class TrendDirection(str, Enum):
    """Directional trend classification based on EMA hierarchy."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class RegimeClassification(BaseModel):
    """Complete quantitative market regime assessment for an underlying asset."""

    symbol: str = Field(description="Underlying ticker symbol")
    regime: MarketRegime = Field(description="Classified market regime")
    recommended_strategy: str = Field(description="Optimal options spread structure")
    trend_direction: TrendDirection = Field(description="Macro trend direction")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score of classification")

    # Volatility Metrics
    current_iv: float = Field(ge=0.0, description="Current implied volatility (annualized decimal)")
    ivr: float = Field(ge=0.0, le=100.0, description="Implied Volatility Rank (0-100)")
    ivp: float = Field(ge=0.0, le=100.0, description="Implied Volatility Percentile (0-100)")
    historical_vol_cc: float = Field(ge=0.0, description="Close-to-Close HV (annualized decimal)")
    historical_vol_parkinson: float = Field(ge=0.0, description="Parkinson High-Low HV (annualized decimal)")
    vol_premium: float = Field(description="Volatility risk premium (IV - HV_cc in decimal)")

    # Technical Indicators
    adx: float = Field(ge=0.0, le=100.0, description="Average Directional Index (14-period)")
    plus_di: float = Field(ge=0.0, description="Positive Directional Indicator (+DI)")
    minus_di: float = Field(ge=0.0, description="Negative Directional Indicator (-DI)")
    ema_20: float = Field(gt=0.0, description="20-day Exponential Moving Average")
    ema_50: float = Field(gt=0.0, description="50-day Exponential Moving Average")
    ema_200: float = Field(gt=0.0, description="200-day Exponential Moving Average")

    as_of: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of regime analysis",
    )

    def to_signal_event(self) -> SignalEvent:
        """Converts regime classification into an event for the EventBus."""
        return SignalEvent(
            symbol=self.symbol,
            regime=self.regime.value,
            signal_type=self.recommended_strategy,
            confidence=self.confidence,
            metadata={
                "ivr": round(self.ivr, 2),
                "ivp": round(self.ivp, 2),
                "adx": round(self.adx, 2),
                "trend": self.trend_direction.value,
                "vol_premium": round(self.vol_premium, 4),
                "current_iv": round(self.current_iv, 4),
                "hv_cc": round(self.historical_vol_cc, 4),
                "hv_parkinson": round(self.historical_vol_parkinson, 4),
            },
        )


class RegimeDetector:
    """
    Mathematical regime detector computing IV Rank, Parkinson/Close-to-Close HV,
    ADX, and EMA trend filters for autonomous strategy selection.
    """

    @staticmethod
    def calculate_ivr(current_iv: float, iv_series: Union[List[float], np.ndarray, pd.Series]) -> float:
        """
        Calculates Implied Volatility Rank (IVR) over 252 trading days.
        IVR = ((Current IV - Min IV) / (Max IV - Min IV)) * 100
        """
        arr = np.asarray(iv_series, dtype=float)
        arr = arr[~np.isnan(arr)]

        if len(arr) == 0:
            return 50.0

        iv_min = float(np.min(arr))
        iv_max = float(np.max(arr))

        if iv_max <= iv_min or math.isclose(iv_max, iv_min, abs_tol=1e-6):
            return 50.0

        raw_ivr = ((current_iv - iv_min) / (iv_max - iv_min)) * 100.0
        return float(np.clip(raw_ivr, 0.0, 100.0))

    @staticmethod
    def calculate_ivp(current_iv: float, iv_series: Union[List[float], np.ndarray, pd.Series]) -> float:
        """
        Calculates Implied Volatility Percentile (IVP) over past year.
        Percentage of trading days where IV was strictly below current IV.
        """
        arr = np.asarray(iv_series, dtype=float)
        arr = arr[~np.isnan(arr)]

        if len(arr) == 0:
            return 50.0

        days_below = np.sum(arr < current_iv)
        percentile = (days_below / len(arr)) * 100.0
        return float(np.clip(percentile, 0.0, 100.0))

    @staticmethod
    def calculate_close_to_close_hv(
        close_prices: Union[List[float], np.ndarray, pd.Series],
        trading_days: int = 252,
    ) -> float:
        """
        Calculates annualized Historical Volatility using log returns:
        HV = std(ln(C_t / C_{t-1})) * sqrt(trading_days)
        """
        closes = np.asarray(close_prices, dtype=float)
        closes = closes[~np.isnan(closes)]

        if len(closes) < 2:
            return 0.0

        log_returns = np.diff(np.log(closes))
        if len(log_returns) < 2:
            return 0.0

        daily_std = float(np.std(log_returns, ddof=1))
        annualized_hv = daily_std * math.sqrt(trading_days)
        return float(annualized_hv)

    @staticmethod
    def calculate_parkinson_hv(
        high_prices: Union[List[float], np.ndarray, pd.Series],
        low_prices: Union[List[float], np.ndarray, pd.Series],
        trading_days: int = 252,
    ) -> float:
        """
        Calculates Parkinson Historical Volatility using High-Low ranges:
        sigma = sqrt( 1 / (4 * ln(2) * N) * sum(ln(H_i / L_i)^2) ) * sqrt(trading_days)
        """
        highs = np.asarray(high_prices, dtype=float)
        lows = np.asarray(low_prices, dtype=float)

        valid_mask = (~np.isnan(highs)) & (~np.isnan(lows)) & (highs > 0) & (lows > 0) & (highs >= lows)
        highs = highs[valid_mask]
        lows = lows[valid_mask]

        n = len(highs)
        if n == 0:
            return 0.0

        hl_ratio = np.log(highs / lows)
        sum_sq = np.sum(hl_ratio ** 2)

        variance = sum_sq / (4.0 * math.log(2.0) * n)
        annualized_hv = math.sqrt(variance) * math.sqrt(trading_days)
        return float(annualized_hv)

    @staticmethod
    def calculate_adx(
        high_prices: Union[List[float], np.ndarray, pd.Series],
        low_prices: Union[List[float], np.ndarray, pd.Series],
        close_prices: Union[List[float], np.ndarray, pd.Series],
        period: int = 14,
    ) -> Tuple[float, float, float]:
        """
        Calculates Average Directional Index (ADX) and Directional Movement (+DI, -DI).

        Returns:
            Tuple of (adx, plus_di, minus_di)
        """
        highs = np.asarray(high_prices, dtype=float)
        lows = np.asarray(low_prices, dtype=float)
        closes = np.asarray(close_prices, dtype=float)

        n = len(closes)
        if n <= period + 1:
            return 20.0, 20.0, 20.0  # Safe default if insufficient history

        # Calculate True Range and Directional Movements
        tr = np.zeros(n)
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)

        tr[0] = highs[0] - lows[0]
        for i in range(1, n):
            hl = highs[i] - lows[i]
            hpc = abs(highs[i] - closes[i - 1])
            lpc = abs(lows[i] - closes[i - 1])
            tr[i] = max(hl, hpc, lpc)

            up_move = highs[i] - highs[i - 1]
            down_move = lows[i - 1] - lows[i]

            if up_move > down_move and up_move > 0:
                plus_dm[i] = up_move
            else:
                plus_dm[i] = 0.0

            if down_move > up_move and down_move > 0:
                minus_dm[i] = down_move
            else:
                minus_dm[i] = 0.0

        # Wilder's Smoothing
        smoothed_tr = np.zeros(n)
        smoothed_pdm = np.zeros(n)
        smoothed_mdm = np.zeros(n)

        smoothed_tr[period] = np.sum(tr[1:period + 1])
        smoothed_pdm[period] = np.sum(plus_dm[1:period + 1])
        smoothed_mdm[period] = np.sum(minus_dm[1:period + 1])

        for i in range(period + 1, n):
            smoothed_tr[i] = smoothed_tr[i - 1] - (smoothed_tr[i - 1] / period) + tr[i]
            smoothed_pdm[i] = smoothed_pdm[i - 1] - (smoothed_pdm[i - 1] / period) + plus_dm[i]
            smoothed_mdm[i] = smoothed_mdm[i - 1] - (smoothed_mdm[i - 1] / period) + minus_dm[i]

        plus_di = np.zeros(n)
        minus_di = np.zeros(n)
        dx = np.zeros(n)

        for i in range(period, n):
            pdi = (100.0 * smoothed_pdm[i] / smoothed_tr[i]) if smoothed_tr[i] > 0 else 0.0
            mdi = (100.0 * smoothed_mdm[i] / smoothed_tr[i]) if smoothed_tr[i] > 0 else 0.0
            plus_di[i] = pdi
            minus_di[i] = mdi

            di_sum = pdi + mdi
            di_diff = abs(pdi - mdi)
            dx[i] = (100.0 * di_diff / di_sum) if di_sum > 0 else 0.0

        # ADX is Wilder smoothed DX
        adx_idx_start = 2 * period
        if n < adx_idx_start:
            return float(dx[-1]), float(plus_di[-1]), float(minus_di[-1])

        adx = np.zeros(n)
        adx[adx_idx_start - 1] = np.mean(dx[period:adx_idx_start])
        for i in range(adx_idx_start, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

        return float(adx[-1]), float(plus_di[-1]), float(minus_di[-1])

    @staticmethod
    def calculate_emas(close_prices: Union[List[float], np.ndarray, pd.Series]) -> Tuple[float, float, float]:
        """
        Calculates Exponential Moving Averages for periods 20, 50, and 200.
        Returns:
            Tuple of (ema_20, ema_50, ema_200)
        """
        series = pd.Series(close_prices, dtype=float)
        ema20 = float(series.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(series.ewm(span=50, adjust=False).mean().iloc[-1])
        ema200 = float(series.ewm(span=200, adjust=False).mean().iloc[-1])
        return ema20, ema50, ema200

    @classmethod
    def classify_regime(
        cls,
        symbol: str,
        current_iv: float,
        iv_history: Union[List[float], np.ndarray, pd.Series],
        high_prices: Union[List[float], np.ndarray, pd.Series],
        low_prices: Union[List[float], np.ndarray, pd.Series],
        close_prices: Union[List[float], np.ndarray, pd.Series],
    ) -> RegimeClassification:
        """
        Comprehensive market regime classification synthesizing IV Rank, Historical Vol,
        ADX, and EMA trend direction.
        """
        # 1. Volatility Metrics
        ivr = cls.calculate_ivr(current_iv, iv_history)
        ivp = cls.calculate_ivp(current_iv, iv_history)
        hv_cc = cls.calculate_close_to_close_hv(close_prices)
        hv_parkinson = cls.calculate_parkinson_hv(high_prices, low_prices)
        vol_premium = current_iv - hv_cc

        # 2. Trend & Momentum Indicators
        adx, plus_di, minus_di = cls.calculate_adx(high_prices, low_prices, close_prices, period=14)
        ema20, ema50, ema200 = cls.calculate_emas(close_prices)

        # 3. Determine Trend Direction
        if ema20 > ema50 > ema200 and plus_di > minus_di:
            trend = TrendDirection.BULLISH
        elif ema20 < ema50 < ema200 and minus_di > plus_di:
            trend = TrendDirection.BEARISH
        else:
            trend = TrendDirection.NEUTRAL

        # 4. Regime Decision Rules
        # HIGH_IV Threshold: IVR > 50
        # TRENDING Threshold: ADX >= 25
        is_high_iv = ivr > 50.0
        is_trending = adx >= 25.0

        if is_high_iv and not is_trending:
            regime = MarketRegime.HIGH_IV_RANGEBOUND
            strategy = "Iron Condor"
            confidence = min(0.95, 0.60 + (ivr - 50.0) / 100.0)

        elif is_high_iv and is_trending:
            regime = MarketRegime.HIGH_IV_TRENDING
            if trend == TrendDirection.BULLISH:
                strategy = "Bull Put Credit Spread"
            elif trend == TrendDirection.BEARISH:
                strategy = "Bear Call Credit Spread"
            else:
                strategy = "Bull Put Credit Spread" if plus_di > minus_di else "Bear Call Credit Spread"
            confidence = min(0.95, 0.65 + (ivr - 50.0) / 150.0 + (adx - 25.0) / 100.0)

        elif not is_high_iv and is_trending:
            regime = MarketRegime.LOW_IV_TRENDING
            if trend == TrendDirection.BULLISH:
                strategy = "Bull Call Debit Spread"
            elif trend == TrendDirection.BEARISH:
                strategy = "Bear Put Debit Spread"
            else:
                strategy = "Vertical Debit Spread"
            confidence = min(0.90, 0.60 + (adx - 25.0) / 100.0)

        else:
            # LOW_IV and CHOP (IVR <= 50, ADX < 25)
            regime = MarketRegime.LOW_IV_CHOP
            strategy = "Cash Preservation"
            confidence = 0.85

        return RegimeClassification(
            symbol=symbol.upper(),
            regime=regime,
            recommended_strategy=strategy,
            trend_direction=trend,
            confidence=round(confidence, 2),
            current_iv=current_iv,
            ivr=ivr,
            ivp=ivp,
            historical_vol_cc=hv_cc,
            historical_vol_parkinson=hv_parkinson,
            vol_premium=vol_premium,
            adx=adx,
            plus_di=plus_di,
            minus_di=minus_di,
            ema_20=ema20,
            ema_50=ema50,
            ema_200=ema200,
        )
