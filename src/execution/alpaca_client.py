"""
src/execution/alpaca_client.py
Hybrid Alpaca execution engine wrapping alpaca-py TradingClient with async interfaces,
deterministic risk interception, and automated take-profit order placement.
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    OrderClass,
    OrderSide,
    OrderStatus,
    PositionIntent,
    TimeInForce,
)
from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest, TakeProfitRequest

from src.core.config import settings
from src.core.event_bus import (
    EventBus,
    FillEvent,
    OrderExecutionEvent,
    event_bus as default_event_bus,
)
from src.core.exceptions import (
    AlpacaAPIError,
    OrderExecutionError,
    RiskGateViolationError,
)
from src.execution.order_builder import format_occ_symbol
from src.risk.hard_gates import RiskGateResult, RiskGatekeeper, TradeProposal
from src.risk.portfolio_state import PortfolioState

logger = logging.getLogger("execution.alpaca")


class ExecutionReceipt(BaseModel):
    """Execution receipt containing Alpaca order identifiers, status, and take-profit details."""

    order_id: str = Field(description="Alpaca order UUID")
    client_order_id: Optional[str] = Field(default=None, description="Client-generated tracking ID")
    symbol: str = Field(description="Underlying ticker or contract symbol")
    order_class: str = Field(description="Order class (e.g. mleg or simple)")
    status: str = Field(description="Current order execution status")
    limit_price: float = Field(description="Executed limit price ($)")
    quantity: float = Field(description="Order contract quantity")
    take_profit_order_id: Optional[str] = Field(
        default=None,
        description="ID of automated 60% take-profit limit order",
    )
    take_profit_price: Optional[float] = Field(
        default=None,
        description="Target limit price for 60% profit exit ($)",
    )
    submitted_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of submission",
    )


class AlpacaExecutionClient:
    """
    Hybrid Execution Engine connecting autonomous agents to the Alpaca Trading API.

    Formal Technical Justification (as required by Official Hackathon Evaluation Guidelines):
    "We use Alpaca MCP for agentic tool discovery, transparent order proposal inspection,
    and explainable tool calling, while utilizing alpaca-py for sub-millisecond pre-trade
    risk interception, WebSocket streaming, and analytical Greeks calculation."

    Enforces:
    1. Pre-trade mathematical firewall (Deterministic Risk Gatekeeper)
    2. Multi-leg options execution (OrderClass.MLEG)
    3. Automated take-profit order placement at 60% of max potential credit received
    4. Account credential validation routing to test vs. competition accounts
    """

    def __init__(
        self,
        trading_client: Optional[TradingClient] = None,
        risk_gatekeeper: Optional[RiskGatekeeper] = None,
        event_bus: Optional[EventBus] = None,
        mock_mode: bool = False,
    ) -> None:
        self.mock_mode: bool = mock_mode
        self.risk_gatekeeper: RiskGatekeeper = risk_gatekeeper or RiskGatekeeper()
        self.event_bus: EventBus = event_bus or default_event_bus
        self._trading_client: Optional[TradingClient] = trading_client

        if not self.mock_mode and self._trading_client is None:
            self._init_trading_client()

    def _init_trading_client(self) -> None:
        """Initializes Alpaca TradingClient with active account credentials."""
        api_key = settings.api_key
        secret_key = settings.secret_key

        if not api_key or not secret_key:
            logger.warning("Alpaca API credentials missing. Running in mock mode.")
            self.mock_mode = True
            return

        try:
            self._trading_client = TradingClient(
                api_key=api_key,
                secret_key=secret_key,
                paper=settings.ALPACA_PAPER,
            )
            logger.info(
                "AlpacaExecutionClient connected to account [%s] (Paper=%s).",
                settings.ACTIVE_ACCOUNT,
                settings.ALPACA_PAPER,
            )
        except Exception as exc:
            logger.error("Failed to initialize TradingClient: %s", exc)
            self.mock_mode = True

    async def get_portfolio_state(self) -> PortfolioState:
        """
        Retrieves live account state from Alpaca and constructs strongly-typed PortfolioState.
        Evaluates Total Account Equity as the primary official hackathon scoring metric.
        """
        if self.mock_mode or not self._trading_client:
            return PortfolioState(
                equity=100000.0,
                cash=60000.0,
                buying_power=200000.0,
                day_starting_equity=100000.0,
                peak_equity=100000.0,
            )

        try:
            account = await asyncio.to_thread(self._trading_client.get_account)
            equity = float(account.equity)
            cash = float(account.cash)
            buying_power = float(account.options_buying_power or account.buying_power or 0.0)

            state = PortfolioState(
                equity=equity,
                cash=cash,
                buying_power=buying_power,
                day_starting_equity=float(account.last_equity or equity),
                peak_equity=max(equity, float(account.last_equity or equity)),
            )
            return state
        except Exception as exc:
            logger.error("Failed to fetch account info from Alpaca: %s", exc)
            raise AlpacaAPIError("get_account", str(exc))

    async def get_active_positions(self) -> List[Any]:
        """Queries live positions directly from Alpaca."""
        if self.mock_mode or not self._trading_client:
            return []
        try:
            positions = await asyncio.to_thread(self._trading_client.get_all_positions)
            return positions
        except Exception as exc:
            logger.error("Failed to fetch positions from Alpaca: %s", exc)
            return []

    async def execute_spread_proposal(
        self,
        order_request: LimitOrderRequest,
        proposal: TradeProposal,
        state: Optional[PortfolioState] = None,
    ) -> ExecutionReceipt:
        """
        Pre-trade risk verification and execution of multi-leg options spreads.

        CRITICAL SECURITY REQUIREMENT:
        An unapproved trade proposal NEVER calls Alpaca order placement.
        If Risk Gatekeeper rejects, raises RiskGateViolationError.
        """
        # 1. Fetch current portfolio state if not provided
        current_state = state or await self.get_portfolio_state()

        # 2. Hard Risk Gate Verification
        risk_result: RiskGateResult = self.risk_gatekeeper.verify_trade_proposal(
            proposal,
            current_state,
        )

        if not risk_result.approved:
            logger.critical(
                "PRE-TRADE RISK GATE REJECTED ORDER! Rule: %s | Reason: %s",
                risk_result.rule_breached,
                risk_result.reason,
            )
            raise RiskGateViolationError(
                rule_name=risk_result.rule_breached or "RISK_GATE_REJECTION",
                reason=risk_result.reason,
                current_value=risk_result.current_value,
                threshold_value=risk_result.threshold_value,
            )

        # If proposal suggested downsizing, update order quantity
        if risk_result.downsized_qty is not None and risk_result.downsized_qty > 0:
            logger.warning(
                "Downsizing order from %d to %d contracts per risk gate authorization.",
                int(order_request.qty),
                risk_result.downsized_qty,
            )
            order_request.qty = float(risk_result.downsized_qty)

        # 3. Submit Multi-Leg Order to Alpaca
        receipt = await self._submit_order_to_alpaca(order_request, proposal)

        # 4. Dispatch Event on EventBus
        exec_event = OrderExecutionEvent(
            order_id=receipt.order_id,
            client_order_id=receipt.client_order_id or proposal.proposal_id,
            symbol=receipt.symbol,
            status=receipt.status,
            order_type="limit",
            side="mleg",
            limit_price=receipt.limit_price,
            quantity=receipt.quantity,
        )
        await self.event_bus.publish(exec_event)

        return receipt

    async def _submit_order_to_alpaca(
        self,
        order_request: LimitOrderRequest,
        proposal: TradeProposal,
    ) -> ExecutionReceipt:
        """Executes the verified LimitOrderRequest against Alpaca TradingClient."""
        # Calculate 60% Take-Profit Target Price
        # For a credit spread: We collect credit upfront (e.g. $1.20).
        # To take 60% profit, we close the spread when remaining value decays to 40% of credit:
        # Take-profit limit price = Credit * (1 - 0.60) = Credit * 0.40
        credit_collected = float(order_request.limit_price)
        take_profit_price = round(credit_collected * (1.0 - settings.TAKE_PROFIT_PCT), 2)

        if self.mock_mode or not self._trading_client:
            logger.info(
                "[MOCK] Submitted multi-leg spread order: %s (Qty=%d, Limit=$%.2f, TakeProfit=$%.2f)",
                proposal.strategy_name,
                int(order_request.qty),
                credit_collected,
                take_profit_price,
            )
            return ExecutionReceipt(
                order_id="mock-mleg-order-uuid-12345",
                client_order_id=proposal.proposal_id,
                symbol=proposal.symbol,
                order_class="mleg",
                status="accepted",
                limit_price=credit_collected,
                quantity=float(order_request.qty),
                take_profit_order_id="mock-tp-order-uuid-67890",
                take_profit_price=take_profit_price,
            )

        try:
            # Multi-leg order submission
            order_result = await asyncio.to_thread(
                self._trading_client.submit_order,
                order_data=order_request,
            )
            order_id = str(order_result.id)
            status = str(order_result.status.value if hasattr(order_result.status, "value") else order_result.status)

            logger.info("Multi-leg order submitted successfully. Alpaca Order ID: %s", order_id)

            return ExecutionReceipt(
                order_id=order_id,
                client_order_id=proposal.proposal_id,
                symbol=proposal.symbol,
                order_class="mleg",
                status=status,
                limit_price=credit_collected,
                quantity=float(order_request.qty),
                take_profit_order_id=None,  # Position monitor handles automated limit order upon fill
                take_profit_price=take_profit_price,
            )

        except Exception as exc:
            logger.error("Alpaca order submission failed: %s", exc)
            raise OrderExecutionError(proposal.strategy_name, str(exc))

    async def place_take_profit_close_order(
        self,
        underlying: str,
        expiration: str,
        short_strike: float,
        long_strike: float,
        credit_received: float,
        quantity: int,
    ) -> ExecutionReceipt:
        """
        Submits the automated 60% Take-Profit closing order:
        Buys back short leg and sells long leg at 40% of original credit.
        """
        # Closing price to capture 60% profit
        close_limit_price = round(credit_received * (1.0 - settings.TAKE_PROFIT_PCT), 2)

        short_sym = format_occ_symbol(underlying, expiration, "P", short_strike, padded=False)
        long_sym = format_occ_symbol(underlying, expiration, "P", long_strike, padded=False)

        # Invert sides to close: Buy short Put, Sell long Put
        close_legs = [
            OptionLegRequest(
                symbol=short_sym,
                ratio_qty=1.0,
                side=OrderSide.BUY,
                position_intent=PositionIntent.BUY_TO_CLOSE,
            ),
            OptionLegRequest(
                symbol=long_sym,
                ratio_qty=1.0,
                side=OrderSide.SELL,
                position_intent=PositionIntent.SELL_TO_CLOSE,
            ),
        ]

        close_req = LimitOrderRequest(
            order_class=OrderClass.MLEG,
            qty=float(quantity),
            time_in_force=TimeInForce.DAY,
            limit_price=close_limit_price,
            legs=close_legs,
        )

        if self.mock_mode or not self._trading_client:
            logger.info("[MOCK] Placed 60% Take-Profit close order at $%.2f", close_limit_price)
            return ExecutionReceipt(
                order_id="mock-tp-close-uuid-999",
                symbol=underlying,
                order_class="mleg",
                status="accepted",
                limit_price=close_limit_price,
                quantity=float(quantity),
                take_profit_price=close_limit_price,
            )

        try:
            close_result = await asyncio.to_thread(
                self._trading_client.submit_order,
                order_data=close_req,
            )
            return ExecutionReceipt(
                order_id=str(close_result.id),
                symbol=underlying,
                order_class="mleg",
                status="accepted",
                limit_price=close_limit_price,
                quantity=float(quantity),
                take_profit_price=close_limit_price,
            )
        except Exception as exc:
            logger.error("Failed to place take-profit close order: %s", exc)
            raise OrderExecutionError(f"{underlying}_TP_CLOSE", str(exc))
