"""
src/data/greeks_engine.py
Vectorized Black-Scholes pricing engine, analytical Greeks computation,
and numerical Implied Volatility (IV) solver with Newton-Raphson and bisection fallback.

Mathematical Conventions:
- S: Spot price of underlying asset ($)
- K: Strike price of option ($)
- T: Time to expiration in annual units (DTE / 365.0)
- r: Annualized continuous risk-free interest rate (decimal, e.g. 0.045 = 4.5%)
- q: Annualized continuous dividend yield (decimal, default 0.0)
- sigma: Annualized volatility (decimal, e.g. 0.20 = 20%)

Greeks Units:
- Delta (Δ): First derivative dV/dS. Unitless (-1.0 to +1.0).
- Gamma (Γ): Second derivative d²V/dS². Change in delta per $1 move in underlying.
- Theta (Θ): Time decay dV/dt, expressed per calendar day (1/365 year) in dollars per share.
- Vega (ν): Sensitivity dV/d(sigma), expressed per 1 percentage point (0.01) change in volatility.
- Rho (ρ): Sensitivity dV/dr, expressed per 1 percentage point (0.01) change in interest rate.
"""

import math
from typing import Literal, Union
import numpy as np
from pydantic import BaseModel, Field
from scipy.stats import norm

OptionType = Literal["call", "put", "C", "P"]


class GreeksResult(BaseModel):
    """Strongly-typed container for Black-Scholes pricing and analytical Greeks."""

    iv: float = Field(ge=0.0, description="Annualized implied volatility (decimal)")
    delta: float = Field(description="Delta sensitivity (-1.0 to 1.0)")
    gamma: float = Field(ge=0.0, description="Gamma sensitivity (rate of change of delta per $1)")
    theta: float = Field(description="Theta time decay per calendar day in dollars per share")
    vega: float = Field(ge=0.0, description="Vega sensitivity per 1% change in volatility in dollars per share")
    rho: float = Field(description="Rho sensitivity per 1% change in interest rate")
    theoretical_price: float = Field(ge=0.0, description="Black-Scholes theoretical option price")


def _standardize_option_type(option_type: OptionType) -> Literal["call", "put"]:
    """Normalizes option type string to 'call' or 'put'."""
    opt = str(option_type).lower().strip()
    if opt in ("c", "call"):
        return "call"
    if opt in ("p", "put"):
        return "put"
    raise ValueError(f"Invalid option type '{option_type}'. Expected 'call', 'put', 'C', or 'P'.")


def black_scholes_price(
    spot: float,
    strike: float,
    time_to_exp: float,
    rate: float,
    vol: float,
    option_type: OptionType = "call",
    dividend_yield: float = 0.0,
) -> float:
    """
    Computes theoretical option price using the Black-Scholes-Merton model with continuous dividend yield.

    Args:
        spot: Underlying asset price (S > 0)
        strike: Option strike price (K > 0)
        time_to_exp: Time to expiration in years (T = DTE / 365.0)
        rate: Risk-free rate (e.g. 0.045 for 4.5%)
        vol: Implied volatility (e.g. 0.20 for 20%)
        option_type: 'call' or 'put'
        dividend_yield: Continuous dividend yield (default 0.0)

    Returns:
        Theoretical option price in dollars per share.
    """
    opt = _standardize_option_type(option_type)

    if spot <= 0.0 or strike <= 0.0:
        return 0.0

    # At or past expiration, price equals intrinsic value
    if time_to_exp <= 1e-7:
        if opt == "call":
            return max(0.0, spot - strike)
        return max(0.0, strike - spot)

    # If zero volatility, option price equals discounted intrinsic
    if vol <= 1e-7:
        df_r = math.exp(-rate * time_to_exp)
        df_q = math.exp(-dividend_yield * time_to_exp)
        if opt == "call":
            return max(0.0, spot * df_q - strike * df_r)
        return max(0.0, strike * df_r - spot * df_q)

    sqrt_t = math.sqrt(time_to_exp)
    d1 = (math.log(spot / strike) + (rate - dividend_yield + 0.5 * vol * vol) * time_to_exp) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t

    df_r = math.exp(-rate * time_to_exp)
    df_q = math.exp(-dividend_yield * time_to_exp)

    if opt == "call":
        price = spot * df_q * norm.cdf(d1) - strike * df_r * norm.cdf(d2)
    else:
        price = strike * df_r * norm.cdf(-d2) - spot * df_q * norm.cdf(-d1)

    return max(0.0, float(price))


def calculate_greeks(
    spot: float,
    strike: float,
    time_to_exp: float,
    rate: float,
    vol: float,
    option_type: OptionType = "call",
    dividend_yield: float = 0.0,
) -> GreeksResult:
    """
    Calculates analytical Greeks and theoretical price under Black-Scholes assumptions.

    Returns:
        GreeksResult containing iv, delta, gamma, theta (daily), vega (per 1% vol), rho, and price.
    """
    opt = _standardize_option_type(option_type)

    # Edge case: expired or immediate expiry
    if time_to_exp <= 1e-7 or vol <= 1e-7:
        price = black_scholes_price(spot, strike, time_to_exp, rate, vol, opt, dividend_yield)
        if opt == "call":
            delta = 1.0 if spot > strike else (0.5 if spot == strike else 0.0)
        else:
            delta = -1.0 if spot < strike else (-0.5 if spot == strike else 0.0)
        return GreeksResult(
            iv=vol,
            delta=delta,
            gamma=0.0,
            theta=0.0,
            vega=0.0,
            rho=0.0,
            theoretical_price=price,
        )

    sqrt_t = math.sqrt(time_to_exp)
    d1 = (math.log(spot / strike) + (rate - dividend_yield + 0.5 * vol * vol) * time_to_exp) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t

    df_r = math.exp(-rate * time_to_exp)
    df_q = math.exp(-dividend_yield * time_to_exp)
    pdf_d1 = norm.pdf(d1)

    # 1. Delta (Δ)
    if opt == "call":
        delta = df_q * norm.cdf(d1)
    else:
        delta = -df_q * norm.cdf(-d1)

    # 2. Gamma (Γ) - identical for call and put
    gamma = (df_q * pdf_d1) / (spot * vol * sqrt_t)

    # 3. Vega (ν) - per 1% change in vol (divide raw vega by 100)
    raw_vega = spot * df_q * sqrt_t * pdf_d1
    vega_1pct = raw_vega / 100.0

    # 4. Theta (Θ) - daily decay (annual theta / 365)
    term1 = -(spot * df_q * vol * pdf_d1) / (2.0 * sqrt_t)
    if opt == "call":
        theta_annual = term1 - rate * strike * df_r * norm.cdf(d2) + dividend_yield * spot * df_q * norm.cdf(d1)
    else:
        theta_annual = term1 + rate * strike * df_r * norm.cdf(-d2) - dividend_yield * spot * df_q * norm.cdf(-d1)
    theta_daily = theta_annual / 365.0

    # 5. Rho (ρ) - per 1% change in interest rate (divide by 100)
    if opt == "call":
        rho = (strike * time_to_exp * df_r * norm.cdf(d2)) / 100.0
    else:
        rho = (-strike * time_to_exp * df_r * norm.cdf(-d2)) / 100.0

    # 6. Theoretical Price
    price = black_scholes_price(spot, strike, time_to_exp, rate, vol, opt, dividend_yield)

    return GreeksResult(
        iv=vol,
        delta=float(delta),
        gamma=float(gamma),
        theta=float(theta_daily),
        vega=float(vega_1pct),
        rho=float(rho),
        theoretical_price=float(price),
    )


def calculate_implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    time_to_exp: float,
    rate: float,
    option_type: OptionType = "call",
    dividend_yield: float = 0.0,
    max_iter: int = 100,
    precision: float = 1e-5,
) -> float:
    """
    Solves for Implied Volatility (IV) given a market option price.
    Uses high-speed Newton-Raphson iteration with automatic fallback to robust Bisection.

    Args:
        market_price: Midpoint or traded market price of option ($)
        spot: Underlying spot price ($)
        strike: Option strike price ($)
        time_to_exp: Time to expiration in years (DTE / 365.0)
        rate: Risk-free rate (e.g. 0.045)
        option_type: 'call' or 'put'
        dividend_yield: Dividend yield (default 0.0)
        max_iter: Maximum iterations
        precision: Absolute convergence error tolerance

    Returns:
        Implied volatility as annualized decimal (e.g. 0.225 = 22.5%).
    """
    opt = _standardize_option_type(option_type)

    if market_price <= 0.0 or spot <= 0.0 or strike <= 0.0 or time_to_exp <= 1e-7:
        return 0.0

    # Theoretical minimum boundary (intrinsic value discounted)
    df_r = math.exp(-rate * time_to_exp)
    df_q = math.exp(-dividend_yield * time_to_exp)
    if opt == "call":
        intrinsic = max(0.0, spot * df_q - strike * df_r)
    else:
        intrinsic = max(0.0, strike * df_r - spot * df_q)

    if market_price <= intrinsic:
        return 0.0001

    # Initial guess using Brenner-Subrahmanyam approximation for ATM options
    # sigma_approx = sqrt(2*pi / T) * (Price / S)
    sigma = math.sqrt(2.0 * math.pi / time_to_exp) * (market_price / spot)
    sigma = max(0.05, min(sigma, 1.5))

    # Phase 1: Newton-Raphson iteration
    for _ in range(max_iter):
        price = black_scholes_price(spot, strike, time_to_exp, rate, sigma, opt, dividend_yield)
        diff = price - market_price

        if abs(diff) < precision:
            return float(sigma)

        # Raw vega = dPrice / dSigma
        sqrt_t = math.sqrt(time_to_exp)
        d1 = (math.log(spot / strike) + (rate - dividend_yield + 0.5 * sigma * sigma) * time_to_exp) / (sigma * sqrt_t)
        vega = spot * df_q * sqrt_t * norm.pdf(d1)

        if vega < 1e-6:
            # Vega too small for Newton-Raphson, break out to bisection
            break

        sigma_new = sigma - diff / vega

        # If Newton shoots outside reasonable volatility bounds, switch to bisection
        if sigma_new <= 0.001 or sigma_new >= 5.0:
            break

        if abs(sigma_new - sigma) < precision:
            return float(sigma_new)

        sigma = sigma_new

    # Phase 2: Robust Bisection Fallback
    low = 0.0001
    high = 5.0

    for _ in range(max_iter):
        mid = (low + high) / 2.0
        price_mid = black_scholes_price(spot, strike, time_to_exp, rate, mid, opt, dividend_yield)
        diff = price_mid - market_price

        if abs(diff) < precision or (high - low) / 2.0 < precision:
            return float(mid)

        if diff > 0:
            high = mid
        else:
            low = mid

    return float((low + high) / 2.0)
