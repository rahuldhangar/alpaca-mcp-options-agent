"""
tests/unit/test_greeks_engine.py
Unit tests validating Black-Scholes pricing, analytical Greeks,
numerical Implied Volatility solver, and OCC option chain parser.
"""

from datetime import date, timedelta
import math
import pytest

from src.core.exceptions import OCCFormattingError
from src.data.chain_parser import (
    OptionChainParser,
    parse_occ_symbol,
)
from src.data.greeks_engine import (
    black_scholes_price,
    calculate_greeks,
    calculate_implied_volatility,
)


# ------------------------------------------------------------------------------
# 1. Black-Scholes Formula & Benchmark Tests
# ------------------------------------------------------------------------------

def test_black_scholes_known_benchmark_values() -> None:
    """
    Standard quantitative benchmark:
    S = 100.0, K = 100.0, T = 1.0 year, r = 0.05 (5%), sigma = 0.20 (20%), q = 0.0.
    Standard theoretical values:
    Call Price = 10.4506
    Put Price  = 5.5735
    """
    s, k, t, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.20

    call_price = black_scholes_price(s, k, t, r, sigma, option_type="call")
    put_price = black_scholes_price(s, k, t, r, sigma, option_type="put")

    assert pytest.approx(call_price, abs=0.001) == 10.4506
    assert pytest.approx(put_price, abs=0.001) == 5.5735

    # Put-Call Parity: C - P = S - K * exp(-r*T)
    parity_diff = call_price - put_price
    expected_parity = s - k * math.exp(-r * t)
    assert pytest.approx(parity_diff, abs=0.0001) == expected_parity


def test_analytical_half_year_benchmark_values() -> None:
    """
    PROMPT-P05 Specification Benchmark:
    Stock = 100.0, Strike = 100.0, T = 0.5 (6 months), r = 0.05, IV = 0.20
    Expected Values:
    Call Price = 6.8887
    Put Price  = 4.4197
    Call Delta = 0.5977
    Put Delta  = -0.4023
    """
    s, k, t, r, sigma = 100.0, 100.0, 0.5, 0.05, 0.20

    call_price = black_scholes_price(s, k, t, r, sigma, option_type="call")
    put_price = black_scholes_price(s, k, t, r, sigma, option_type="put")

    assert pytest.approx(call_price, abs=0.001) == 6.8887
    assert pytest.approx(put_price, abs=0.001) == 4.4197

    call_greeks = calculate_greeks(s, k, t, r, sigma, option_type="call")
    put_greeks = calculate_greeks(s, k, t, r, sigma, option_type="put")

    assert pytest.approx(call_greeks.delta, abs=0.001) == 0.5977
    assert pytest.approx(put_greeks.delta, abs=0.001) == -0.4023
    assert pytest.approx(call_price - put_price, abs=0.0001) == (s - k * math.exp(-r * t))


def test_delta_strict_boundaries_across_spectrum() -> None:
    """
    PROMPT-P05 Specification:
    Ensures Call Delta is strictly within [0.0, 1.0] and Put Delta is strictly within [-1.0, 0.0]
    across an extensive grid of spots, strikes, and expirations.
    """
    spots = [20.0, 50.0, 100.0, 250.0, 500.0, 800.0]
    strikes = [30.0, 50.0, 100.0, 200.0, 550.0, 750.0]
    times = [1.0 / 365.0, 7.0 / 365.0, 30.0 / 365.0, 90.0 / 365.0, 1.0]
    volatilities = [0.10, 0.25, 0.50, 0.90]

    for s in spots:
        for k in strikes:
            for t in times:
                for v in volatilities:
                    cg = calculate_greeks(s, k, t, 0.045, v, "call")
                    pg = calculate_greeks(s, k, t, 0.045, v, "put")

                    assert 0.0 <= cg.delta <= 1.0, f"Call delta {cg.delta} violated [0, 1] at S={s}, K={k}"
                    assert -1.0 <= pg.delta <= 0.0, f"Put delta {pg.delta} violated [-1, 0] at S={s}, K={k}"


def test_asymptotic_convergence_deep_itm_and_otm() -> None:
    """
    PROMPT-P05 Specification:
    Deep ITM and Deep OTM asymptotic behavior:
    - Deep ITM Call: Price -> S - K*exp(-rT), Delta -> 1.0, Gamma -> 0.0
    - Deep OTM Call: Price -> 0.0, Delta -> 0.0, Gamma -> 0.0
    - Deep ITM Put:  Price -> K*exp(-rT) - S, Delta -> -1.0, Gamma -> 0.0
    - Deep OTM Put:  Price -> 0.0, Delta -> 0.0, Gamma -> 0.0
    """
    k, t, r, vol = 100.0, 0.25, 0.05, 0.20
    df = math.exp(-r * t)

    # 1. Deep ITM Call (S = 1000, K = 100)
    cg_itm = calculate_greeks(1000.0, k, t, r, vol, "call")
    assert pytest.approx(cg_itm.theoretical_price, abs=0.01) == (1000.0 - k * df)
    assert pytest.approx(cg_itm.delta, abs=0.0001) == 1.0
    assert cg_itm.gamma < 1e-5

    # 2. Deep OTM Call (S = 10, K = 100)
    cg_otm = calculate_greeks(10.0, k, t, r, vol, "call")
    assert pytest.approx(cg_otm.theoretical_price, abs=0.0001) == 0.0
    assert pytest.approx(cg_otm.delta, abs=0.0001) == 0.0
    assert cg_otm.gamma < 1e-5

    # 3. Deep ITM Put (S = 10, K = 100)
    pg_itm = calculate_greeks(10.0, k, t, r, vol, "put")
    assert pytest.approx(pg_itm.theoretical_price, abs=0.01) == (k * df - 10.0)
    assert pytest.approx(pg_itm.delta, abs=0.0001) == -1.0
    assert pg_itm.gamma < 1e-5

    # 4. Deep OTM Put (S = 1000, K = 100)
    pg_otm = calculate_greeks(1000.0, k, t, r, vol, "put")
    assert pytest.approx(pg_otm.theoretical_price, abs=0.0001) == 0.0
    assert pytest.approx(pg_otm.delta, abs=0.0001) == 0.0
    assert pg_otm.gamma < 1e-5


def test_black_scholes_analytical_greeks_benchmarks() -> None:
    """
    Verifies analytical Greeks against known textbook benchmarks:
    S = 100.0, K = 100.0, T = 1.0, r = 0.05, sigma = 0.20
    Expected:
    Call Delta ~ 0.6368
    Put Delta  ~ -0.3632
    Gamma      ~ 0.0188
    Vega (1%)  ~ 0.3752
    """
    s, k, t, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.20

    call_greeks = calculate_greeks(s, k, t, r, sigma, option_type="call")
    put_greeks = calculate_greeks(s, k, t, r, sigma, option_type="put")

    # Delta
    assert pytest.approx(call_greeks.delta, abs=0.002) == 0.6368
    assert pytest.approx(put_greeks.delta, abs=0.002) == -0.3632

    # Gamma is identical for both Call and Put
    assert pytest.approx(call_greeks.gamma, abs=0.001) == 0.0188
    assert pytest.approx(put_greeks.gamma, abs=0.001) == 0.0188

    # Vega per 1% vol is identical for Call and Put
    assert pytest.approx(call_greeks.vega, abs=0.002) == 0.3752
    assert pytest.approx(put_greeks.vega, abs=0.002) == 0.3752

    # Theta decay is negative for both long call and put
    assert call_greeks.theta < 0.0
    assert put_greeks.theta < 0.0


def test_black_scholes_boundary_conditions() -> None:
    """Tests expiration, deep ITM, and deep OTM boundary conditions."""
    # At expiration (T = 0)
    assert black_scholes_price(105.0, 100.0, 0.0, 0.05, 0.20, "call") == 5.0
    assert black_scholes_price(95.0, 100.0, 0.0, 0.05, 0.20, "call") == 0.0
    assert black_scholes_price(95.0, 100.0, 0.0, 0.05, 0.20, "put") == 5.0

    # Deep OTM
    deep_otm_call = black_scholes_price(100.0, 200.0, 0.1, 0.05, 0.20, "call")
    assert deep_otm_call < 0.0001


# ------------------------------------------------------------------------------
# 2. Implied Volatility Solver Tests
# ------------------------------------------------------------------------------

def test_implied_volatility_solver_roundtrip() -> None:
    """
    Verifies that calculate_implied_volatility recovers known input volatility.
    """
    s, k, t, r = 560.0, 555.0, 30.0 / 365.0, 0.045
    target_vols = [0.12, 0.18, 0.25, 0.40, 0.65]

    for expected_iv in target_vols:
        # Generate market price from Black-Scholes
        call_price = black_scholes_price(s, k, t, r, expected_iv, "call")
        put_price = black_scholes_price(s, k, t, r, expected_iv, "put")

        # Invert via solver
        solved_call_iv = calculate_implied_volatility(call_price, s, k, t, r, "call")
        solved_put_iv = calculate_implied_volatility(put_price, s, k, t, r, "put")

        assert pytest.approx(solved_call_iv, abs=0.001) == expected_iv
        assert pytest.approx(solved_put_iv, abs=0.001) == expected_iv


def test_implied_volatility_edge_cases() -> None:
    """Verifies solver behavior with below-intrinsic and zero prices."""
    s, k, t, r = 100.0, 90.0, 0.1, 0.05
    # Call price below intrinsic (10.0)
    iv = calculate_implied_volatility(5.0, s, k, t, r, "call")
    assert iv == 0.0001

    # Zero market price
    assert calculate_implied_volatility(0.0, s, k, t, r, "call") == 0.0


# ------------------------------------------------------------------------------
# 3. OCC Symbol Decomposition & Parsing Tests
# ------------------------------------------------------------------------------

def test_parse_occ_symbol_standard_cases() -> None:
    """Verifies decomposition of standard OCC 21-character symbols."""
    root, exp, opt_type, strike = parse_occ_symbol("SPY260918C00560000")
    assert root == "SPY"
    assert exp == "2026-09-18"
    assert opt_type == "call"
    assert strike == 560.0

    root, exp, opt_type, strike = parse_occ_symbol("QQQ261016P00480000")
    assert root == "QQQ"
    assert exp == "2026-10-16"
    assert opt_type == "put"
    assert strike == 480.0

    root, exp, opt_type, strike = parse_occ_symbol("NVDA261218C00125500")
    assert root == "NVDA"
    assert exp == "2026-12-18"
    assert opt_type == "call"
    assert strike == 125.50


def test_parse_occ_symbol_invalid_formats() -> None:
    """Verifies that malformed OCC symbols raise OCCFormattingError."""
    with pytest.raises(OCCFormattingError):
        parse_occ_symbol("SPY_INVALID_FORMAT")

    with pytest.raises(OCCFormattingError):
        parse_occ_symbol("SPY260918X00560000")  # 'X' is neither C nor P


# ------------------------------------------------------------------------------
# 4. Option Chain Parser & Strike Ladder Tests
# ------------------------------------------------------------------------------

def test_option_chain_parser_strike_ladders() -> None:
    """Verifies OptionChainParser converts raw contracts into organized strike ladders."""
    today = date.today()
    target_exp = (today + timedelta(days=30)).isoformat()
    yy = target_exp[2:4]
    mm = target_exp[5:7]
    dd = target_exp[8:10]
    exp_code = f"{yy}{mm}{dd}"

    mock_contracts = [
        # 550 Call and Put
        {"symbol": f"SPY{exp_code}C00550000", "tradable": True, "open_interest": 500},
        {"symbol": f"SPY{exp_code}P00550000", "tradable": True, "open_interest": 600},
        # 555 Call and Put
        {"symbol": f"SPY{exp_code}C00555000", "tradable": True, "open_interest": 800},
        {"symbol": f"SPY{exp_code}P00555000", "tradable": True, "open_interest": 900},
        # Illiquid contract (low OI)
        {"symbol": f"SPY{exp_code}P00530000", "tradable": True, "open_interest": 2},
    ]

    quotes_map = {
        f"SPY{exp_code}C00550000": {"bid": 12.0, "ask": 12.10, "implied_volatility": 0.20},
        f"SPY{exp_code}P00550000": {"bid": 2.50, "ask": 2.55, "implied_volatility": 0.21},
        f"SPY{exp_code}C00555000": {"bid": 8.0, "ask": 8.10, "implied_volatility": 0.19},
        f"SPY{exp_code}P00555000": {"bid": 3.80, "ask": 3.85, "implied_volatility": 0.20},
    }

    chain = OptionChainParser.parse_contracts(
        contracts=mock_contracts,
        underlying_symbol="SPY",
        underlying_price=560.0,
        quotes_map=quotes_map,
        min_open_interest=10,
        min_dte=14,
        max_dte=45,
    )

    assert chain.underlying_symbol == "SPY"
    assert chain.underlying_price == 560.0
    assert target_exp in chain.expirations

    ladder = chain.get_expiration(target_exp)
    assert ladder is not None
    assert 550.0 in ladder.strikes
    assert 555.0 in ladder.strikes

    # Test put spread extraction
    # Bull Put spread: Short 555 Put (higher strike), Long 550 Put (lower strike)
    spread = ladder.get_put_spread(short_strike=555.0, long_strike=550.0)
    assert spread is not None
    short_put, long_put = spread
    assert short_put.strike_price == 555.0
    assert long_put.strike_price == 550.0
    assert short_put.mid == pytest.approx(3.825, 0.001)
    assert long_put.mid == pytest.approx(2.525, 0.001)

    # Net credit = Short Mid - Long Mid = 3.825 - 2.525 = 1.30
    net_credit = short_put.mid - long_put.mid
    assert pytest.approx(net_credit, 0.001) == 1.30


def test_chain_parser_slippage_guard_filtering() -> None:
    """Verifies that contracts with wide bid-ask spread are marked not liquid."""
    today = date.today()
    target_exp = (today + timedelta(days=25)).isoformat()
    exp_code = target_exp[2:4] + target_exp[5:7] + target_exp[8:10]

    sym_wide = f"QQQ{exp_code}C00480000"
    mock_contracts = [
        {"symbol": sym_wide, "tradable": True, "open_interest": 1000},
    ]
    # Spread is $0.50 (exceeds $0.15 max limit)
    quotes_map = {
        sym_wide: {"bid": 2.00, "ask": 2.50, "implied_volatility": 0.22},
    }

    chain = OptionChainParser.parse_contracts(
        contracts=mock_contracts,
        underlying_symbol="QQQ",
        underlying_price=480.0,
        quotes_map=quotes_map,
        max_slippage_dollars=0.15,
    )

    ladder = chain.get_expiration(target_exp)
    assert ladder is not None
    contract = ladder.strikes[480.0].call
    assert contract is not None
    assert contract.is_liquid is False  # Must be rejected by slippage guard
