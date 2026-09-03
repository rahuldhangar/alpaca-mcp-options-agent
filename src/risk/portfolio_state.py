"""
src/risk/portfolio_state.py
Strongly-typed portfolio state tracking for the Alpaca options trading system.

Focuses strictly on the official hackathon scoring metric:
Total Account Equity (portfolio equity reflecting positions, exercises, assignments, and cash).
"""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field

from src.core.config import settings


class PortfolioState(BaseModel):
    """
    Real-time portfolio state representation.
    Tracks equity, cash, buying power, portfolio Greeks, drawdown, and margin utilization.
    """

    # Primary Official Scoring Dimension
    equity: float = Field(
        ge=0.0,
        description="Total Account Equity ($) - Official Hackathon Scoring Metric",
    )
    cash: float = Field(
        description="Available unallocated cash balance ($)",
    )
    buying_power: float = Field(
        ge=0.0,
        description="Current options buying power from Alpaca ($)",
    )

    # Benchmark Equities for Circuit Breakers
    day_starting_equity: float = Field(
        ge=0.0,
        description="Portfolio equity at the start of the trading day (09:30 a.m. ET)",
    )
    peak_equity: float = Field(
        ge=0.0,
        description="Highest equity watermark achieved during the competition window",
    )

    # Real-Time Performance & Drawdown
    current_daily_pnl: float = Field(
        default=0.0,
        description="Intraday dollar P&L (equity - day_starting_equity)",
    )
    current_daily_pnl_pct: float = Field(
        default=0.0,
        description="Intraday percentage P&L fraction",
    )
    current_drawdown_dollars: float = Field(
        default=0.0,
        ge=0.0,
        description="Dollar drawdown from peak equity (peak_equity - equity)",
    )
    current_drawdown_pct: float = Field(
        default=0.0,
        ge=0.0,
        description="Percentage drawdown from peak equity fraction",
    )

    # Portfolio Aggregate Greeks
    net_delta: float = Field(
        default=0.0,
        description="Net portfolio delta sensitivity to $1 underlying move",
    )
    net_gamma: float = Field(
        default=0.0,
        description="Net portfolio gamma sensitivity",
    )
    net_theta: float = Field(
        default=0.0,
        description="Net portfolio theta decay per calendar day ($)",
    )
    net_vega: float = Field(
        default=0.0,
        description="Net portfolio vega per 1% change in volatility ($)",
    )

    # Margin and Exposure
    margin_utilized: float = Field(
        default=0.0,
        ge=0.0,
        description="Total margin collateral currently committed to open spreads ($)",
    )
    open_positions_count: int = Field(
        default=0,
        ge=0,
        description="Number of open options spread positions",
    )

    last_updated: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of state update",
    )

    def model_post_init(self, __context: Optional[dict] = None) -> None:
        """Initializes derived drawdown and daily PnL metrics upon instantiation."""
        self._recalculate_metrics()

    def _recalculate_metrics(self) -> None:
        """Recalculates daily PnL and peak-to-trough drawdown."""
        # Ensure peak equity is at least current equity
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity

        # Daily PnL
        self.current_daily_pnl = self.equity - self.day_starting_equity
        if self.day_starting_equity > 0:
            self.current_daily_pnl_pct = self.current_daily_pnl / self.day_starting_equity
        else:
            self.current_daily_pnl_pct = 0.0

        # Drawdown from peak
        self.current_drawdown_dollars = max(0.0, self.peak_equity - self.equity)
        if self.peak_equity > 0:
            self.current_drawdown_pct = self.current_drawdown_dollars / self.peak_equity
        else:
            self.current_drawdown_pct = 0.0

    def update_equity(self, new_equity: float, cash: Optional[float] = None) -> None:
        """
        Updates equity and cash balances, updating high-water marks and drawdowns.
        """
        self.equity = max(0.0, float(new_equity))
        if cash is not None:
            self.cash = float(cash)
        self._recalculate_metrics()
        self.last_updated = datetime.now(timezone.utc)

    # --------------------------------------------------------------------------
    # Derived Properties & Risk Gate Helper Queries
    # --------------------------------------------------------------------------
    @property
    def margin_utilization_pct(self) -> float:
        """Current fraction of equity tied up in margin collateral."""
        return (self.margin_utilized / self.equity) if self.equity > 0 else 0.0

    @property
    def available_margin_headroom(self) -> float:
        """Remaining margin dollar capacity before reaching the 40% ceiling."""
        max_allowed = self.equity * settings.MAX_MARGIN_UTILIZATION_PCT
        return max(0.0, max_allowed - self.margin_utilized)

    @property
    def max_risk_per_trade_dollars(self) -> float:
        """Maximum allowable dollar risk for any single trade based on current equity (5%)."""
        return self.equity * settings.MAX_RISK_PER_TRADE_PCT

    @property
    def is_daily_circuit_breaker_tripped(self) -> bool:
        """Returns True if intraday loss exceeds the 5% daily breaker threshold."""
        return self.current_daily_pnl_pct <= -settings.DAILY_LOSS_CIRCUIT_BREAKER_PCT

    @property
    def is_drawdown_circuit_breaker_tripped(self) -> bool:
        """Returns True if portfolio drawdown from peak exceeds the 10% emergency stop limit."""
        return self.current_drawdown_pct >= settings.MAX_PORTFOLIO_DRAWDOWN_PCT
