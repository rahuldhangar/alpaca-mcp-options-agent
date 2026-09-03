"""
src/risk/hard_gates.py
Deterministic mathematical risk gatekeeper enforcing non-negotiable capital protection boundaries.

Enforces:
- Rule 1: Max Capital Risk per trade <= 5.0% of Total Account Equity ($5,000 max on $100k).
- Rule 2: Max Portfolio Margin Utilization <= 40.0% of Total Account Equity ($40,000 max).
- Rule 3: Daily Loss Circuit Breaker <= 5.0% of Day Starting Equity ($5,000 loss).
- Rule 4: Absolute Portfolio Drawdown <= 10.0% from Peak Equity ($10,000 drawdown).
- Rule 5: Target DTE Universe: Primary (14–45 DTE), Tactical event-driven (0–7 DTE).
- Rule 6: Bid-Ask Slippage Guard: Mid-spread <= 3.0% and <= $0.15/contract.
"""

from datetime import datetime, timezone
import math
import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.core.config import settings
from src.risk.portfolio_state import PortfolioState


class TradeProposal(BaseModel):
    """Container for a structured options trade proposal awaiting risk authorization."""

    proposal_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique proposal tracking ID",
    )
    symbol: str = Field(description="Underlying ticker symbol (e.g. SPY)")
    strategy_name: str = Field(description="Strategy name (e.g. Bull Put Credit Spread)")
    legs: List[Dict[str, Any]] = Field(default_factory=list, description="Contract legs specification")
    quantity: int = Field(default=1, gt=0, description="Proposed number of contracts")
    max_loss_per_contract: float = Field(gt=0.0, description="Maximum potential loss per single contract ($)")
    target_credit_per_contract: float = Field(default=0.0, ge=0.0, description="Target net credit collected ($)")
    required_margin_per_contract: float = Field(gt=0.0, description="Collateral / margin requirement per contract ($)")
    dte: int = Field(ge=0, description="Days to expiration")
    is_tactical: bool = Field(default=False, description="Flag for short-duration tactical event trades (0-7 DTE)")

    # Slippage Metrics
    spread_slippage_pct: Optional[float] = Field(default=None, description="Bid-ask spread / mid-price fraction")
    spread_slippage_dollars: Optional[float] = Field(default=None, description="Absolute bid-ask spread in dollars")

    # LLM Hypothesis & Metadata
    thesis: Optional[str] = Field(default=None, description="Qualitative rationale and LLM hypothesis")
    max_profit: Optional[float] = Field(default=None, description="Max potential dollar profit")
    max_loss: Optional[float] = Field(default=None, description="Max potential dollar loss")
    ivr: Optional[float] = Field(default=None, description="Implied Volatility Rank at proposal time")
    regime: Optional[str] = Field(default=None, description="Market regime classification")
    underlying: Optional[str] = Field(default=None, description="Underlying symbol alias")

    @property
    def total_capital_at_risk(self) -> float:
        """Total dollar capital exposed to maximum loss across all contracts."""
        return self.max_loss_per_contract * self.quantity

    @property
    def total_required_margin(self) -> float:
        """Total portfolio margin collateral required across all contracts."""
        return self.required_margin_per_contract * self.quantity


class RiskGateResult(BaseModel):
    """Structured decision output from the RiskGatekeeper."""

    approved: bool = Field(description="True if trade is fully authorized for execution")
    reason: str = Field(description="Detailed authorization or rejection explanation")
    downsized_qty: Optional[int] = Field(
        default=None,
        description="Suggested safe contract quantity if original proposal exceeded capital limits",
    )
    rule_breached: Optional[str] = Field(
        default=None,
        description="Identifier of breached rule (e.g. MAX_RISK, MARGIN_CEILING, DAILY_BREAKER)",
    )
    current_value: Optional[float] = Field(default=None, description="Measured quantitative value")
    threshold_value: Optional[float] = Field(default=None, description="Hard mathematical threshold")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of risk evaluation",
    )


class RiskGatekeeper:
    """
    Deterministic mathematical firewall intercepting all order proposals before Alpaca routing.
    No LLM, agent, or strategy engine can bypass these hard limits.
    """

    def __init__(
        self,
        max_risk_pct: Optional[float] = None,
        max_margin_pct: Optional[float] = None,
        daily_loss_breaker_pct: Optional[float] = None,
        max_drawdown_pct: Optional[float] = None,
        max_slippage_pct: Optional[float] = None,
        max_slippage_dollars: Optional[float] = None,
    ) -> None:
        self.max_risk_pct: float = max_risk_pct or settings.MAX_RISK_PER_TRADE_PCT
        self.max_margin_pct: float = max_margin_pct or settings.MAX_MARGIN_UTILIZATION_PCT
        self.daily_loss_breaker_pct: float = daily_loss_breaker_pct or settings.DAILY_LOSS_CIRCUIT_BREAKER_PCT
        self.max_drawdown_pct: float = max_drawdown_pct or settings.MAX_PORTFOLIO_DRAWDOWN_PCT
        self.max_slippage_pct: float = max_slippage_pct or settings.MAX_SLIPPAGE_PCT
        self.max_slippage_dollars: float = max_slippage_dollars or settings.MAX_SLIPPAGE_DOLLARS

    def verify_trade_proposal(
        self,
        proposal: TradeProposal,
        state: PortfolioState,
    ) -> RiskGateResult:
        """
        Executes all 6 deterministic mathematical risk gates against a proposed trade.

        Returns:
            RiskGateResult with approved flag, reason, and optional downsized quantity.
        """
        # ----------------------------------------------------------------------
        # Gate 1: Absolute Portfolio Drawdown Emergency Stop (Rule 4)
        # ----------------------------------------------------------------------
        if state.current_drawdown_pct >= self.max_drawdown_pct:
            return RiskGateResult(
                approved=False,
                rule_breached="EMERGENCY_STOP",
                current_value=state.current_drawdown_pct,
                threshold_value=self.max_drawdown_pct,
                reason=(
                    f"EMERGENCY STOP ACTIVE: Peak drawdown of {state.current_drawdown_pct * 100:.2f}% "
                    f"exceeds {self.max_drawdown_pct * 100:.1f}% maximum boundary. All new trading halted."
                ),
            )

        # ----------------------------------------------------------------------
        # Gate 2: Daily Loss Circuit Breaker (Rule 3)
        # ----------------------------------------------------------------------
        if state.current_daily_pnl_pct <= -self.daily_loss_breaker_pct:
            return RiskGateResult(
                approved=False,
                rule_breached="DAILY_BREAKER",
                current_value=abs(state.current_daily_pnl_pct),
                threshold_value=self.daily_loss_breaker_pct,
                reason=(
                    f"DAILY CIRCUIT BREAKER TRIPPED: Intraday loss of {abs(state.current_daily_pnl_pct) * 100:.2f}% "
                    f"exceeds {self.daily_loss_breaker_pct * 100:.1f}% limit. Trading halted for remainder of day."
                ),
            )

        # ----------------------------------------------------------------------
        # Gate 3: Target DTE Universe (Rule 5)
        # ----------------------------------------------------------------------
        if proposal.is_tactical:
            # Tactical event trades: 0 - 7 DTE
            if proposal.dte < settings.TACTICAL_DTE_MIN or proposal.dte > settings.TACTICAL_DTE_MAX:
                return RiskGateResult(
                    approved=False,
                    rule_breached="INVALID_DTE_TACTICAL",
                    current_value=float(proposal.dte),
                    threshold_value=float(settings.TACTICAL_DTE_MAX),
                    reason=(
                        f"Tactical trade DTE ({proposal.dte}) falls outside allowed tactical window "
                        f"({settings.TACTICAL_DTE_MIN} - {settings.TACTICAL_DTE_MAX} DTE)."
                    ),
                )
        else:
            # Primary swing structures: 14 - 45 DTE
            if proposal.dte < settings.PRIMARY_DTE_MIN or proposal.dte > settings.PRIMARY_DTE_MAX:
                return RiskGateResult(
                    approved=False,
                    rule_breached="INVALID_DTE_PRIMARY",
                    current_value=float(proposal.dte),
                    threshold_value=float(settings.PRIMARY_DTE_MAX),
                    reason=(
                        f"Primary trade DTE ({proposal.dte}) falls outside allowed primary window "
                        f"({settings.PRIMARY_DTE_MIN} - {settings.PRIMARY_DTE_MAX} DTE)."
                    ),
                )

        # ----------------------------------------------------------------------
        # Gate 4: Bid-Ask Slippage Guard (Rule 6)
        # ----------------------------------------------------------------------
        if proposal.spread_slippage_dollars is not None and proposal.spread_slippage_dollars > self.max_slippage_dollars:
            return RiskGateResult(
                approved=False,
                rule_breached="SLIPPAGE_DOLLAR_EXCEEDED",
                current_value=proposal.spread_slippage_dollars,
                threshold_value=self.max_slippage_dollars,
                reason=(
                    f"Bid-ask spread (${proposal.spread_slippage_dollars:.2f}) exceeds maximum "
                    f"allowable limit of ${self.max_slippage_dollars:.2f} per contract."
                ),
            )

        if proposal.spread_slippage_pct is not None and proposal.spread_slippage_pct > self.max_slippage_pct:
            return RiskGateResult(
                approved=False,
                rule_breached="SLIPPAGE_PCT_EXCEEDED",
                current_value=proposal.spread_slippage_pct,
                threshold_value=self.max_slippage_pct,
                reason=(
                    f"Bid-ask spread percentage ({proposal.spread_slippage_pct * 100:.2f}%) exceeds "
                    f"maximum allowable limit of {self.max_slippage_pct * 100:.1f}%."
                ),
            )

        # ----------------------------------------------------------------------
        # Gate 5: Max Capital Risk Per Trade (Rule 1: 5.0% of Total Account Equity)
        # ----------------------------------------------------------------------
        max_capital_risk = state.equity * self.max_risk_pct
        proposed_risk = proposal.total_capital_at_risk

        if proposed_risk > max_capital_risk:
            # Calculate maximum contract quantity that fits within boundary
            max_qty = math.floor(max_capital_risk / proposal.max_loss_per_contract)
            if max_qty < 1:
                return RiskGateResult(
                    approved=False,
                    rule_breached="MAX_CAPITAL_RISK",
                    current_value=proposed_risk,
                    threshold_value=max_capital_risk,
                    downsized_qty=None,
                    reason=(
                        f"Capital risk of single contract (${proposal.max_loss_per_contract:.2f}) exceeds "
                        f"5.0% maximum allowable risk ceiling (${max_capital_risk:.2f}). Order cannot be executed."
                    ),
                )
            else:
                return RiskGateResult(
                    approved=False,
                    rule_breached="MAX_CAPITAL_RISK",
                    current_value=proposed_risk,
                    threshold_value=max_capital_risk,
                    downsized_qty=max_qty,
                    reason=(
                        f"Proposed capital risk (${proposed_risk:.2f} for {proposal.quantity} contracts) "
                        f"exceeds 5.0% limit (${max_capital_risk:.2f}). Downsize to {max_qty} contracts required."
                    ),
                )

        # ----------------------------------------------------------------------
        # Gate 6: Max Portfolio Margin Utilization Ceiling (Rule 2: 40.0% of Equity)
        # ----------------------------------------------------------------------
        max_allowable_margin = state.equity * self.max_margin_pct
        projected_margin = state.margin_utilized + proposal.total_required_margin
        projected_margin_pct = projected_margin / state.equity if state.equity > 0 else 1.0

        if projected_margin > max_allowable_margin:
            return RiskGateResult(
                approved=False,
                rule_breached="MARGIN_CEILING_EXCEEDED",
                current_value=projected_margin_pct,
                threshold_value=self.max_margin_pct,
                reason=(
                    f"Projected margin utilization ({projected_margin_pct * 100:.2f}% = ${projected_margin:.2f}) "
                    f"breaches 40.0% portfolio ceiling (${max_allowable_margin:.2f})."
                ),
            )

        # ----------------------------------------------------------------------
        # All Hard Risk Gates Passed Successfully
        # ----------------------------------------------------------------------
        return RiskGateResult(
            approved=True,
            reason="All 6 deterministic risk gates passed successfully. Trade authorized for execution.",
            downsized_qty=None,
        )
