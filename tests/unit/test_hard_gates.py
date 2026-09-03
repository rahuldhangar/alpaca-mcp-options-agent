"""
tests/unit/test_hard_gates.py
Exhaustive boundary tests for the deterministic mathematical RiskGatekeeper and CircuitBreakerHandler.
"""

from unittest.mock import MagicMock
import pytest

from src.risk.circuit_breaker import CircuitBreakerAction, CircuitBreakerHandler
from src.risk.hard_gates import RiskGatekeeper, TradeProposal
from src.risk.portfolio_state import PortfolioState


@pytest.fixture
def fresh_portfolio_100k() -> PortfolioState:
    """Standard $100,000 Paper Trading portfolio baseline."""
    return PortfolioState(
        equity=100_000.0,
        cash=60_000.0,
        buying_power=200_000.0,
        day_starting_equity=100_000.0,
        peak_equity=100_000.0,
        margin_utilized=0.0,
    )


@pytest.fixture
def gatekeeper() -> RiskGatekeeper:
    """Standard RiskGatekeeper with Aggressive Hackathon Tier boundaries."""
    return RiskGatekeeper()


# ------------------------------------------------------------------------------
# 1. Capital Risk Per Trade Boundary Tests (5.0% Limit = $5,000 on $100k)
# ------------------------------------------------------------------------------

def test_capital_risk_4_99_pct_approved(fresh_portfolio_100k: PortfolioState, gatekeeper: RiskGatekeeper) -> None:
    """Trade with 4.99% capital risk ($4,990 on $100k) MUST APPROVE."""
    proposal = TradeProposal(
        symbol="SPY",
        strategy_name="Bull Put Spread",
        quantity=1,
        max_loss_per_contract=4_990.0,
        required_margin_per_contract=5_000.0,
        dte=30,
    )
    result = gatekeeper.verify_trade_proposal(proposal, fresh_portfolio_100k)
    assert result.approved is True
    assert result.downsized_qty is None


def test_capital_risk_5_00_pct_approved(fresh_portfolio_100k: PortfolioState, gatekeeper: RiskGatekeeper) -> None:
    """Trade with exactly 5.00% capital risk ($5,000 on $100k) MUST APPROVE."""
    proposal = TradeProposal(
        symbol="SPY",
        strategy_name="Bull Put Spread",
        quantity=1,
        max_loss_per_contract=5_000.0,
        required_margin_per_contract=5_000.0,
        dte=30,
    )
    result = gatekeeper.verify_trade_proposal(proposal, fresh_portfolio_100k)
    assert result.approved is True
    assert result.downsized_qty is None


def test_capital_risk_5_01_pct_rejected(fresh_portfolio_100k: PortfolioState, gatekeeper: RiskGatekeeper) -> None:
    """Trade with 5.01% capital risk ($5,010 on $100k) MUST REJECT."""
    proposal = TradeProposal(
        symbol="SPY",
        strategy_name="Bull Put Spread",
        quantity=1,
        max_loss_per_contract=5_010.0,
        required_margin_per_contract=6_000.0,
        dte=30,
    )
    result = gatekeeper.verify_trade_proposal(proposal, fresh_portfolio_100k)
    assert result.approved is False
    assert result.rule_breached == "MAX_CAPITAL_RISK"
    assert "exceeds 5.0%" in result.reason


def test_capital_risk_multi_contract_downsizing(fresh_portfolio_100k: PortfolioState, gatekeeper: RiskGatekeeper) -> None:
    """Exceeding 5% with multiple contracts must reject and calculate exact safe downsized quantity."""
    # 6 contracts @ $1,000 risk = $6,000 risk (6.0% > 5.0%)
    proposal = TradeProposal(
        symbol="QQQ",
        strategy_name="Bull Put Spread",
        quantity=6,
        max_loss_per_contract=1_000.0,
        required_margin_per_contract=1_200.0,
        dte=25,
    )
    result = gatekeeper.verify_trade_proposal(proposal, fresh_portfolio_100k)
    assert result.approved is False
    assert result.rule_breached == "MAX_CAPITAL_RISK"
    assert result.downsized_qty == 5  # 5 * $1,000 = $5,000 <= $5,000


# ------------------------------------------------------------------------------
# 2. Portfolio Margin Ceiling Boundary Tests (40.0% Ceiling = $40,000 on $100k)
# ------------------------------------------------------------------------------

def test_margin_boundary_39_9_vs_40_1(fresh_portfolio_100k: PortfolioState, gatekeeper: RiskGatekeeper) -> None:
    """Margin at 39.9% MUST APPROVE; Margin at 40.1% MUST REJECT."""
    fresh_portfolio_100k.margin_utilized = 35_000.0  # $35k currently used

    # Case A: Additional $4,900 -> total $39,900 (39.9%) -> APPROVE
    proposal_approve = TradeProposal(
        symbol="IWM",
        strategy_name="Iron Condor",
        quantity=1,
        max_loss_per_contract=1_000.0,
        required_margin_per_contract=4_900.0,
        dte=30,
    )
    res_a = gatekeeper.verify_trade_proposal(proposal_approve, fresh_portfolio_100k)
    assert res_a.approved is True

    # Case B: Additional $5,100 -> total $40,100 (40.1%) -> REJECT
    proposal_reject = TradeProposal(
        symbol="IWM",
        strategy_name="Iron Condor",
        quantity=1,
        max_loss_per_contract=1_000.0,
        required_margin_per_contract=5_100.0,
        dte=30,
    )
    res_b = gatekeeper.verify_trade_proposal(proposal_reject, fresh_portfolio_100k)
    assert res_b.approved is False
    assert res_b.rule_breached == "MARGIN_CEILING_EXCEEDED"
    assert "breaches 40.0%" in res_b.reason


# ------------------------------------------------------------------------------
# 3. Daily Loss Circuit Breaker Boundary Tests (5.0% Loss = $5,000 on $100k)
# ------------------------------------------------------------------------------

def test_daily_loss_circuit_breaker_boundary(gatekeeper: RiskGatekeeper) -> None:
    """Daily loss of $4,999 MUST APPROVE; Daily loss of $5,001 MUST REJECT."""
    standard_proposal = TradeProposal(
        symbol="SPY",
        strategy_name="Bull Put Spread",
        quantity=1,
        max_loss_per_contract=500.0,
        required_margin_per_contract=1_000.0,
        dte=30,
    )

    # Case A: Intraday equity $95,001 -> Loss = $4,999 (4.999%) -> APPROVE
    portfolio_safe = PortfolioState(
        equity=95_001.0,
        cash=50_000.0,
        buying_power=190_000.0,
        day_starting_equity=100_000.0,
        peak_equity=100_000.0,
    )
    res_safe = gatekeeper.verify_trade_proposal(standard_proposal, portfolio_safe)
    assert res_safe.approved is True

    # Case B: Intraday equity $94,999 -> Loss = $5,001 (5.001%) -> TRIGGER DAILY BREAKER
    portfolio_breached = PortfolioState(
        equity=94_999.0,
        cash=50_000.0,
        buying_power=190_000.0,
        day_starting_equity=100_000.0,
        peak_equity=100_000.0,
    )
    res_breached = gatekeeper.verify_trade_proposal(standard_proposal, portfolio_breached)
    assert res_breached.approved is False
    assert res_breached.rule_breached == "DAILY_BREAKER"
    assert "DAILY CIRCUIT BREAKER TRIPPED" in res_breached.reason


# ------------------------------------------------------------------------------
# 4. Absolute Portfolio Drawdown Emergency Stop (10.0% from Peak)
# ------------------------------------------------------------------------------

def test_drawdown_emergency_stop_boundary(gatekeeper: RiskGatekeeper) -> None:
    """Peak equity $100k with current equity $89,900 (10.1% drawdown) triggers EMERGENCY STOP."""
    standard_proposal = TradeProposal(
        symbol="NVDA",
        strategy_name="Bear Call Spread",
        quantity=1,
        max_loss_per_contract=400.0,
        required_margin_per_contract=1_000.0,
        dte=20,
    )

    portfolio_stopped = PortfolioState(
        equity=89_900.0,
        cash=50_000.0,
        buying_power=150_000.0,
        day_starting_equity=92_000.0,
        peak_equity=100_000.0,
    )
    result = gatekeeper.verify_trade_proposal(standard_proposal, portfolio_stopped)
    assert result.approved is False
    assert result.rule_breached == "EMERGENCY_STOP"
    assert "EMERGENCY STOP ACTIVE" in result.reason


# ------------------------------------------------------------------------------
# 5. Target DTE Universe Boundary Tests (Primary 14–45 DTE, Tactical 0–7 DTE)
# ------------------------------------------------------------------------------

def test_dte_universe_boundary(fresh_portfolio_100k: PortfolioState, gatekeeper: RiskGatekeeper) -> None:
    """Tests DTE floor and ceiling for primary and tactical trades."""
    base_kwargs = dict(
        symbol="MSFT",
        strategy_name="Bull Put Spread",
        quantity=1,
        max_loss_per_contract=500.0,
        required_margin_per_contract=1_000.0,
    )

    # 1. 10 DTE Primary -> REJECT (Floor is 14)
    p_10_dte = TradeProposal(**base_kwargs, dte=10, is_tactical=False)
    res_10 = gatekeeper.verify_trade_proposal(p_10_dte, fresh_portfolio_100k)
    assert res_10.approved is False
    assert res_10.rule_breached == "INVALID_DTE_PRIMARY"

    # 2. 14 DTE Primary -> APPROVE (Exact floor)
    p_14_dte = TradeProposal(**base_kwargs, dte=14, is_tactical=False)
    assert gatekeeper.verify_trade_proposal(p_14_dte, fresh_portfolio_100k).approved is True

    # 3. 45 DTE Primary -> APPROVE (Exact ceiling)
    p_45_dte = TradeProposal(**base_kwargs, dte=45, is_tactical=False)
    assert gatekeeper.verify_trade_proposal(p_45_dte, fresh_portfolio_100k).approved is True

    # 4. 46 DTE Primary -> REJECT (Above ceiling)
    p_46_dte = TradeProposal(**base_kwargs, dte=46, is_tactical=False)
    assert gatekeeper.verify_trade_proposal(p_46_dte, fresh_portfolio_100k).approved is False

    # 5. Tactical Trade: 5 DTE -> APPROVE (Within 0 - 7)
    p_tactical_ok = TradeProposal(**base_kwargs, dte=5, is_tactical=True)
    assert gatekeeper.verify_trade_proposal(p_tactical_ok, fresh_portfolio_100k).approved is True

    # 6. Tactical Trade: 8 DTE -> REJECT (Above 7)
    p_tactical_bad = TradeProposal(**base_kwargs, dte=8, is_tactical=True)
    assert gatekeeper.verify_trade_proposal(p_tactical_bad, fresh_portfolio_100k).approved is False
    assert p_tactical_bad.is_tactical is True


# ------------------------------------------------------------------------------
# 6. Bid-Ask Slippage Guard Boundary Tests (Spread <= 3.0% and <= $0.15)
# ------------------------------------------------------------------------------

def test_slippage_guard_boundary(fresh_portfolio_100k: PortfolioState, gatekeeper: RiskGatekeeper) -> None:
    """Tests 3.0% spread fraction and $0.15 absolute spread boundaries."""
    base_kwargs = dict(
        symbol="AAPL",
        strategy_name="Bull Put Spread",
        quantity=1,
        max_loss_per_contract=400.0,
        required_margin_per_contract=1_000.0,
        dte=30,
    )

    # 4.0% percentage slippage -> REJECT
    p_pct_bad = TradeProposal(**base_kwargs, spread_slippage_pct=0.04)
    res_pct = gatekeeper.verify_trade_proposal(p_pct_bad, fresh_portfolio_100k)
    assert res_pct.approved is False
    assert res_pct.rule_breached == "SLIPPAGE_PCT_EXCEEDED"

    # 3.0% percentage slippage -> APPROVE
    p_pct_ok = TradeProposal(**base_kwargs, spread_slippage_pct=0.03)
    assert gatekeeper.verify_trade_proposal(p_pct_ok, fresh_portfolio_100k).approved is True

    # $0.16 dollar spread -> REJECT
    p_dlr_bad = TradeProposal(**base_kwargs, spread_slippage_dollars=0.16)
    res_dlr = gatekeeper.verify_trade_proposal(p_dlr_bad, fresh_portfolio_100k)
    assert res_dlr.approved is False
    assert res_dlr.rule_breached == "SLIPPAGE_DOLLAR_EXCEEDED"

    # $0.15 dollar spread -> APPROVE
    p_dlr_ok = TradeProposal(**base_kwargs, spread_slippage_dollars=0.15)
    assert gatekeeper.verify_trade_proposal(p_dlr_ok, fresh_portfolio_100k).approved is True


# ------------------------------------------------------------------------------
# 7. CircuitBreakerHandler Interventions
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_circuit_breaker_handler_daily_halt() -> None:
    """Verifies that daily breaker trips cancel orders and flags halt."""
    mock_trading_client = MagicMock()
    mock_trading_client.cancel_orders.return_value = [MagicMock(), MagicMock()]

    handler = CircuitBreakerHandler(trading_client=mock_trading_client, mock_mode=False)

    breached_state = PortfolioState(
        equity=94_000.0,
        cash=50_000.0,
        buying_power=180_000.0,
        day_starting_equity=100_000.0,
        peak_equity=100_000.0,
    )

    action = await handler.handle_daily_loss_breaker(breached_state)

    assert isinstance(action, CircuitBreakerAction)
    assert action.action_type == "DAILY_HALT"
    assert action.orders_canceled == 2
    assert handler.is_daily_halted is True
    mock_trading_client.cancel_orders.assert_called_once()


@pytest.mark.asyncio
async def test_circuit_breaker_handler_emergency_stop() -> None:
    """Verifies that emergency stop cancels orders, closes positions, and sets emergency state."""
    mock_trading_client = MagicMock()
    mock_trading_client.cancel_orders.return_value = [MagicMock()]
    mock_trading_client.close_all_positions.return_value = [MagicMock(), MagicMock(), MagicMock()]

    handler = CircuitBreakerHandler(trading_client=mock_trading_client, mock_mode=False)

    stopped_state = PortfolioState(
        equity=88_000.0,
        cash=40_000.0,
        buying_power=150_000.0,
        day_starting_equity=95_000.0,
        peak_equity=100_000.0,
    )

    action = await handler.handle_emergency_drawdown_stop(stopped_state)

    assert isinstance(action, CircuitBreakerAction)
    assert action.action_type == "EMERGENCY_STOP"
    assert action.orders_canceled == 1
    assert action.positions_closed == 3
    assert handler.is_emergency_stopped is True
    mock_trading_client.cancel_orders.assert_called_once()
    mock_trading_client.close_all_positions.assert_called_once_with(cancel_orders=True)
