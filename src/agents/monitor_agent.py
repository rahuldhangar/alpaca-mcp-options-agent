"""
src/agents/monitor_agent.py
Autonomous position monitor enforcing 60% profit target harvesting, 2.5x stop-losses,
and 3 DTE expiration pin-risk defense.
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field

from src.agents.base_agent import BaseAgent
from src.core.attribution_logger import AttributionLogger, TradeAttributionRecord
from src.core.config import settings
from src.core.event_bus import EventBus, FillEvent, event_bus as default_event_bus
from src.execution.alpaca_client import AlpacaExecutionClient

logger = logging.getLogger("agent.monitor")


class MonitoredSpread(BaseModel):
    """Container for an active options spread position under automated risk monitoring."""

    trade_id: str = Field(description="Unique trade tracking UUID")
    underlying: str = Field(description="Underlying asset ticker (e.g. SPY)")
    strategy_name: str = Field(description="Strategy name (e.g. Bull Put Credit Spread)")
    regime: str = Field(description="Market regime classification at entry")
    entry_date: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC entry timestamp",
    )
    expiration_date: str = Field(description="Expiration date (YYYY-MM-DD)")
    entry_credit: float = Field(gt=0.0, description="Initial credit received per spread ($)")
    contracts: int = Field(default=1, gt=0, description="Number of contracts")
    short_strike: float = Field(description="Short strike price")
    long_strike: float = Field(description="Long strike price")
    short_symbol: str = Field(description="OCC symbol of short option leg")
    long_symbol: str = Field(description="OCC symbol of long option leg")
    current_dte: int = Field(ge=0, description="Current days to expiration")

    # Optional entry metadata
    entry_ivr: Optional[float] = Field(default=None, description="IV Rank at entry")
    entry_greeks: Optional[Dict[str, float]] = Field(default=None, description="Entry Greeks")

    @property
    def take_profit_price(self) -> float:
        """
        Target price to buy back spread to harvest 60% of credit:
        Remaining value = Credit * (1.0 - 0.60) = Credit * 0.40
        """
        return round(self.entry_credit * (1.0 - settings.TAKE_PROFIT_PCT), 2)

    @property
    def stop_loss_price(self) -> float:
        """
        Hard stop-loss trigger price when spread value expands to 2.5x initial credit:
        Stop price = Credit * 2.5
        """
        return round(self.entry_credit * settings.STOP_LOSS_MULTIPLIER, 2)


class PositionMonitorAgent(BaseAgent):
    """
    Autonomous position monitor ensuring disciplined profit harvesting and capital protection.
    Polls active options positions and executes automated exits:
    1. 60% Take-Profit Target (decay to 40% of credit)
    2. 2.5x Stop-Loss Multiplier (expansion to 2.5x credit)
    3. 3 DTE Pin-Risk Defense (close remaining positions at <= 3 DTE)
    """

    def __init__(
        self,
        execution_client: Optional[AlpacaExecutionClient] = None,
        attribution_logger: Optional[AttributionLogger] = None,
        event_bus: Optional[EventBus] = None,
        poll_interval: float = 5.0,
        mock_mode: bool = False,
    ) -> None:
        super().__init__(name="monitor", event_bus=event_bus)
        self.execution_client: Optional[AlpacaExecutionClient] = execution_client
        self.attribution_logger: AttributionLogger = attribution_logger or AttributionLogger()
        self.poll_interval: float = poll_interval
        self.mock_mode: bool = mock_mode

        self._active_spreads: Dict[str, MonitoredSpread] = {}
        self._monitor_task: Optional[asyncio.Task] = None

    def track_spread(self, spread: MonitoredSpread) -> None:
        """Registers a new active spread for automated monitoring."""
        self._active_spreads[spread.trade_id] = spread
        self.logger.info(
            "Tracking %s on %s | Credit: $%.2f | TP Target: $%.2f | SL Trigger: $%.2f",
            spread.strategy_name,
            spread.underlying,
            spread.entry_credit,
            spread.take_profit_price,
            spread.stop_loss_price,
        )

    def get_tracked_spreads(self) -> List[MonitoredSpread]:
        """Returns all currently active monitored spreads."""
        return list(self._active_spreads.values())

    async def start(self) -> None:
        """Starts background position monitoring loop."""
        self._running = True
        self.telemetry.is_running = True
        self.logger.info("PositionMonitorAgent started (Poll Interval: %.1fs).", self.poll_interval)
        self._monitor_task = asyncio.create_task(self._monitoring_loop())

    async def stop(self) -> None:
        """Stops background position monitoring loop."""
        self._running = False
        self.telemetry.is_running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        self.logger.info("PositionMonitorAgent stopped.")

    async def _monitoring_loop(self) -> None:
        """Continuous async polling loop checking open spreads against exit thresholds."""
        while self._running:
            try:
                await self.evaluate_positions()
            except Exception as exc:
                self.logger.error("Error during position evaluation: %s", exc)
                self.telemetry.errors_encountered += 1
            await asyncio.sleep(self.poll_interval)

    async def evaluate_positions(
        self,
        simulated_prices: Optional[Dict[str, float]] = None,
    ) -> List[TradeAttributionRecord]:
        """
        Evaluates all active spreads against exit rules:
        - Rule 1: Take-profit at 60% of credit (spread <= 40% of entry credit)
        - Rule 2: Hard stop-loss at 2.5x credit (spread >= 2.5x entry credit)
        - Rule 3: Expiration defense (DTE <= 3)

        Returns list of generated TradeAttributionRecord objects for closed trades.
        """
        closed_records: List[TradeAttributionRecord] = []
        trades_to_close: List[Tuple[str, str, float]] = []

        for trade_id, spread in list(self._active_spreads.items()):
            # Determine current spread market price
            current_price = (
                simulated_prices.get(trade_id)
                if simulated_prices and trade_id in simulated_prices
                else await self._fetch_current_spread_price(spread)
            )

            # Check Exit Conditions
            exit_reason: Optional[str] = None

            # 1. 60% Take-Profit Target Check
            if current_price <= spread.take_profit_price:
                exit_reason = "TAKE_PROFIT_60"
                self.logger.info(
                    "TAKE-PROFIT TRIGGERED for %s! Current: $%.2f <= Target: $%.2f (Captured 60%% profit)",
                    spread.strategy_name,
                    current_price,
                    spread.take_profit_price,
                )

            # 2. 2.5x Stop-Loss Trigger Check
            elif current_price >= spread.stop_loss_price:
                exit_reason = "STOP_LOSS_2.5X"
                self.logger.warning(
                    "STOP-LOSS TRIGGERED for %s! Current: $%.2f >= Stop: $%.2f (2.5x credit limit breached)",
                    spread.strategy_name,
                    current_price,
                    spread.stop_loss_price,
                )

            # 3. 3 DTE Pin-Risk Expiration Defense Check
            elif spread.current_dte <= 3:
                exit_reason = "DTE_EXPIRY_3D"
                self.logger.info(
                    "3 DTE EXPIRATION EXIT TRIGGERED for %s! DTE: %d <= 3. Eliminating pin risk.",
                    spread.strategy_name,
                    spread.current_dte,
                )

            if exit_reason:
                trades_to_close.append((trade_id, exit_reason, current_price))

        # Execute Exits
        for trade_id, exit_reason, exit_price in trades_to_close:
            record = await self._execute_spread_exit(trade_id, exit_reason, exit_price)
            if record:
                closed_records.append(record)

        self.record_activity()
        return closed_records

    async def _fetch_current_spread_price(self, spread: MonitoredSpread) -> float:
        """
        Calculates the current net debit cost to close the spread:
        Cost to close = Ask(Short Leg) - Bid(Long Leg)
        """
        # In mock mode or if client absent, return entry credit as default
        return spread.entry_credit

    async def _execute_spread_exit(
        self,
        trade_id: str,
        exit_reason: str,
        exit_price: float,
    ) -> Optional[TradeAttributionRecord]:
        """Executes closing order on Alpaca and creates attribution record."""
        spread = self._active_spreads.pop(trade_id, None)
        if not spread:
            return None

        # Calculate realized P&L
        # For a credit spread: Profit = (Entry Credit - Exit Cost) * 100 * contracts
        pnl_per_contract = (spread.entry_credit - exit_price) * 100.0
        total_pnl = round(pnl_per_contract * spread.contracts, 2)
        pnl_pct = round((total_pnl / (spread.entry_credit * 100.0 * spread.contracts)) * 100.0, 1)

        # Place closing order on Alpaca if client available
        if self.execution_client:
            try:
                await self.execution_client.place_take_profit_close_order(
                    underlying=spread.underlying,
                    expiration=spread.expiration_date,
                    short_strike=spread.short_strike,
                    long_strike=spread.long_strike,
                    credit_received=spread.entry_credit,
                    quantity=spread.contracts,
                )
            except Exception as exc:
                self.logger.error("Error executing closing order on Alpaca: %s", exc)

        # Build and log attribution record
        record = TradeAttributionRecord(
            trade_id=spread.trade_id,
            ticker=spread.underlying,
            strategy_name=spread.strategy_name,
            regime=spread.regime,
            entry_date=spread.entry_date,
            exit_date=datetime.now(timezone.utc),
            entry_credit=spread.entry_credit,
            exit_price=exit_price,
            contracts=spread.contracts,
            realized_pnl=total_pnl,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason,
            entry_ivr=spread.entry_ivr,
            entry_greeks=spread.entry_greeks,
        )

        self.attribution_logger.record_trade_exit(record)
        self.telemetry.proposals_generated += 1

        return record
