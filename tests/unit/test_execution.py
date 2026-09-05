"""
tests/unit/test_execution.py
Unit tests for hybrid execution engine: AlpacaExecutionClient and AlpacaMCPBridge.
Ensures that pre-trade risk gates protect capital and unapproved proposals NEVER call Alpaca.
"""

from unittest.mock import MagicMock, patch
import pytest

from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent
from alpaca.trading.requests import LimitOrderRequest

from src.core.exceptions import RiskGateViolationError
from src.execution.alpaca_client import (
    AlpacaExecutionClient,
    ExecutionReceipt,
    MarketClockState,
)
from src.execution.mcp_bridge import (
    AlpacaMCPBridge,
    MCPAccountInfo,
    MCPOptionContract,
    MCPPosition,
)
from src.execution.order_builder import build_bull_put_spread
from src.risk.hard_gates import RiskGatekeeper, TradeProposal
from src.risk.portfolio_state import PortfolioState


@pytest.fixture
def mock_trading_client() -> MagicMock:
    """Mocked Alpaca TradingClient fixture."""
    client = MagicMock()
    mock_order = MagicMock()
    mock_order.id = "mock-alpaca-order-uuid-999"
    mock_order.status.value = "accepted"
    client.submit_order.return_value = mock_order
    return client


@pytest.fixture
def standard_portfolio_state() -> PortfolioState:
    """Standard $100k account state fixture."""
    return PortfolioState(
        equity=100_000.0,
        cash=60_000.0,
        buying_power=200_000.0,
        day_starting_equity=100_000.0,
        peak_equity=100_000.0,
        margin_utilized=0.0,
    )


# ------------------------------------------------------------------------------
# 1. Pre-Trade Risk Firewall & Execution Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unapproved_trade_proposal_never_calls_alpaca(
    mock_trading_client: MagicMock,
    standard_portfolio_state: PortfolioState,
) -> None:
    """
    CRITICAL SECURITY TEST:
    A proposal that violates deterministic risk boundaries MUST be rejected
    and MUST NEVER execute an Alpaca API order placement call.
    """
    execution_client = AlpacaExecutionClient(
        trading_client=mock_trading_client,
        mock_mode=False,
    )

    # 1. Proposal with 6.0% risk ($6,000 > $5,000 limit)
    order_req, unapproved_proposal = build_bull_put_spread(
        underlying="SPY",
        expiration="2026-09-18",
        short_strike=550.0,
        long_strike=540.0,
        credit=1.00,
        quantity=7,  # 7 * $900 = $6,300 max loss > $5,000 (5% of $100k)
        dte=30,
    )

    with pytest.raises(RiskGateViolationError) as exc_info:
        await execution_client.execute_spread_proposal(
            order_request=order_req,
            proposal=unapproved_proposal,
            state=standard_portfolio_state,
        )

    # Assert that RiskGateViolationError was raised
    assert "MAX_CAPITAL_RISK" in str(exc_info.value.message)

    # Verify that submit_order was NEVER called on Alpaca TradingClient
    mock_trading_client.submit_order.assert_not_called()


@pytest.mark.asyncio
async def test_approved_trade_proposal_executes_mleg_order(
    mock_trading_client: MagicMock,
    standard_portfolio_state: PortfolioState,
) -> None:
    """An approved proposal successfully calls submit_order with OrderClass.MLEG."""
    execution_client = AlpacaExecutionClient(
        trading_client=mock_trading_client,
        mock_mode=False,
    )

    # Valid proposal: 2 contracts @ $380 risk = $760 risk (0.76% of $100k)
    order_req, approved_proposal = build_bull_put_spread(
        underlying="SPY",
        expiration="2026-09-18",
        short_strike=550.0,
        long_strike=545.0,
        credit=1.20,
        quantity=2,
        dte=30,
    )

    receipt = await execution_client.execute_spread_proposal(
        order_request=order_req,
        proposal=approved_proposal,
        state=standard_portfolio_state,
    )

    assert isinstance(receipt, ExecutionReceipt)
    assert receipt.order_id == "mock-alpaca-order-uuid-999"
    assert receipt.limit_price == 1.20
    assert receipt.quantity == 2.0
    # Take-profit calculation: $1.20 * (1 - 0.60) = $0.48
    assert receipt.take_profit_price == 0.48

    mock_trading_client.submit_order.assert_called_once_with(order_data=order_req)


@pytest.mark.asyncio
async def test_place_take_profit_close_order(mock_trading_client: MagicMock) -> None:
    """Verifies that automated 60% take profit close order inverts legs and targets 40% of credit."""
    execution_client = AlpacaExecutionClient(
        trading_client=mock_trading_client,
        mock_mode=False,
    )

    receipt = await execution_client.place_take_profit_close_order(
        underlying="SPY",
        expiration="2026-09-18",
        short_strike=550.0,
        long_strike=545.0,
        credit_received=1.20,
        quantity=2,
    )

    assert receipt.take_profit_price == 0.48
    mock_trading_client.submit_order.assert_called_once()
    called_order = mock_trading_client.submit_order.call_args.kwargs["order_data"]
    assert called_order.limit_price == 0.48
    assert called_order.legs[0].position_intent == PositionIntent.BUY_TO_CLOSE
    assert called_order.legs[1].position_intent == PositionIntent.SELL_TO_CLOSE


# ------------------------------------------------------------------------------
# 2. Alpaca MCP Bridge Tests (v2.3+ Tool Calling)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mcp_bridge_account_info_deserialization() -> None:
    """Verifies typed MCPAccountInfo deserialization from get_account_info tool."""
    mcp_bridge = AlpacaMCPBridge(mock_mode=True)

    account_info = await mcp_bridge.get_account_info()
    assert isinstance(account_info, MCPAccountInfo)
    assert account_info.equity == 100_000.0
    assert account_info.cash == 60_000.0
    assert account_info.buying_power == 200_000.0
    assert account_info.options_trading_level == 3
    assert account_info.status == "ACTIVE"


@pytest.mark.asyncio
async def test_mcp_bridge_positions_and_contracts() -> None:
    """Verifies typed list deserialization for get_all_positions and get_option_contracts."""
    mcp_bridge = AlpacaMCPBridge(mock_mode=True)

    # 1. Positions
    positions = await mcp_bridge.get_all_positions()
    assert len(positions) == 2
    assert isinstance(positions[0], MCPPosition)
    assert positions[0].side == "short"

    # 2. Contracts
    contracts = await mcp_bridge.get_option_contracts("SPY", "2026-09-18")
    assert len(contracts) == 2
    assert isinstance(contracts[0], MCPOptionContract)
    assert contracts[0].underlying_symbol == "SPY"
    assert contracts[0].strike_price == 550.0

    # 3. Emergency cancel all
    cancel_res = await mcp_bridge.cancel_all_orders()
    assert cancel_res.get("status") == "success"


@pytest.mark.asyncio
async def test_find_real_option_spread_legs_mock() -> None:
    """Verifies that find_real_option_spread_legs returns valid spread legs in mock mode."""
    execution_client = AlpacaExecutionClient(mock_mode=True)

    # Test Bull Put Spread in mock mode
    put_legs = await execution_client.find_real_option_spread_legs(
        underlying="NVDA",
        current_price=128.50,
        strategy="Bull Put Credit Spread",
    )
    assert put_legs is not None
    assert put_legs["short_strike"] > put_legs["long_strike"]
    assert put_legs["contract_type"] == "put"
    assert "NVDA" in put_legs["short_symbol"]

    # Test Bear Call Spread in mock mode
    call_legs = await execution_client.find_real_option_spread_legs(
        underlying="TSLA",
        current_price=225.00,
        strategy="Bear Call Credit Spread",
    )
    assert call_legs is not None
    assert call_legs["short_strike"] < call_legs["long_strike"]
    assert call_legs["contract_type"] == "call"
    assert "TSLA" in call_legs["short_symbol"]


@pytest.mark.asyncio
async def test_find_real_option_spread_legs_with_client(mock_trading_client: MagicMock) -> None:
    """Verifies that find_real_option_spread_legs selects listed strikes from TradingClient."""
    from datetime import date, timedelta
    from unittest.mock import MagicMock

    today = date.today()
    exp_date = today + timedelta(days=21)

    c1 = MagicMock(symbol="SPY260925P00530000", strike_price=530.0, expiration_date=exp_date)
    c2 = MagicMock(symbol="SPY260925P00535000", strike_price=535.0, expiration_date=exp_date)
    c3 = MagicMock(symbol="SPY260925P00540000", strike_price=540.0, expiration_date=exp_date)

    mock_resp = MagicMock()
    mock_resp.option_contracts = [c1, c2, c3]
    mock_trading_client.get_option_contracts.return_value = mock_resp

    execution_client = AlpacaExecutionClient(trading_client=mock_trading_client, mock_mode=False)

    legs = await execution_client.find_real_option_spread_legs(
        underlying="SPY",
        current_price=560.0,
        strategy="Bull Put Credit Spread",
    )

    assert legs is not None
    assert legs["short_strike"] == 535.0
    assert legs["long_strike"] == 530.0
    assert legs["short_symbol"] == "SPY260925P00535000"
    assert legs["long_symbol"] == "SPY260925P00530000"
    assert legs["dte"] == 21


# ------------------------------------------------------------------------------
# 3. Market Clock and RTH State Tests
# ------------------------------------------------------------------------------

def test_market_clock_countdown_formatting() -> None:
    """Verifies MarketClockState formats human-readable countdowns accurately."""
    from datetime import datetime, timezone, timedelta

    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)

    # When open: always 0m
    open_clock = MarketClockState(is_open=True, timestamp=now, next_open=now + timedelta(days=2))
    assert open_clock.countdown_to_open_str == "0m"

    # Days + hours + minutes
    closed_clock_days = MarketClockState(
        is_open=False,
        timestamp=now,
        next_open=now + timedelta(days=2, hours=3, minutes=15),
    )
    assert closed_clock_days.countdown_to_open_str == "2d 3h 15m"

    # Hours + minutes
    closed_clock_hours = MarketClockState(
        is_open=False,
        timestamp=now,
        next_open=now + timedelta(hours=4, minutes=45),
    )
    assert closed_clock_hours.countdown_to_open_str == "4h 45m"

    # Minutes only
    closed_clock_minutes = MarketClockState(
        is_open=False,
        timestamp=now,
        next_open=now + timedelta(minutes=25),
    )
    assert closed_clock_minutes.countdown_to_open_str == "25m"


@pytest.mark.asyncio
async def test_get_market_clock_mock_mode() -> None:
    """In mock mode, get_market_clock returns open state without network calls."""
    execution_client = AlpacaExecutionClient(mock_mode=True)
    clock_state = await execution_client.get_market_clock()

    assert isinstance(clock_state, MarketClockState)
    assert clock_state.is_open is True
    assert clock_state.next_open is not None
    assert clock_state.next_close is not None


@pytest.mark.asyncio
async def test_get_market_clock_with_trading_client(mock_trading_client: MagicMock) -> None:
    """Verifies that get_market_clock queries trading_client and parses closed hours."""
    from datetime import datetime, timezone, timedelta

    now = datetime(2026, 9, 5, 20, 0, 0, tzinfo=timezone.utc)
    next_open = now + timedelta(days=2, hours=13, minutes=30)
    next_close = next_open + timedelta(hours=6, minutes=30)

    mock_clock = MagicMock()
    mock_clock.is_open = False
    mock_clock.next_open = next_open
    mock_clock.next_close = next_close
    mock_clock.timestamp = now
    mock_trading_client.get_clock.return_value = mock_clock

    execution_client = AlpacaExecutionClient(trading_client=mock_trading_client, mock_mode=False)
    clock_state = await execution_client.get_market_clock()

    assert isinstance(clock_state, MarketClockState)
    assert clock_state.is_open is False
    assert clock_state.next_open == next_open
    assert clock_state.next_close == next_close
    assert clock_state.countdown_to_open_str == "2d 13h 30m"
    mock_trading_client.get_clock.assert_called_once()


@pytest.mark.asyncio
async def test_get_market_clock_fallback_on_exception(mock_trading_client: MagicMock) -> None:
    """Verifies that API errors default to open gracefully without throwing."""
    mock_trading_client.get_clock.side_effect = RuntimeError("Alpaca rate limit or 500 error")

    execution_client = AlpacaExecutionClient(trading_client=mock_trading_client, mock_mode=False)
    clock_state = await execution_client.get_market_clock()

    assert isinstance(clock_state, MarketClockState)
    assert clock_state.is_open is True


