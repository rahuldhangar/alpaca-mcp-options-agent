"""
tests/unit/test_monitor_agent.py
Comprehensive unit tests for PositionMonitorAgent and AttributionLogger:
1. 60% Take-Profit Target Trigger ($0.40 of credit remaining)
2. 2.5x Stop-Loss Multiplier Trigger (expansion to 2.5x credit)
3. 3 DTE Pin-Risk Expiration Defense Trigger
4. Holding state (no triggers breached)
5. AttributionLogger analytics, metrics, and markdown presentation generation
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, MagicMock
import pytest

from src.agents.monitor_agent import MonitoredSpread, PositionMonitorAgent
from src.core.attribution_logger import (
    AttributionLogger,
    AttributionSummary,
    TradeAttributionRecord,
)


@pytest.fixture
def temp_attribution_log(tmp_path: Path) -> Path:
    """Provides a temporary file path for attribution logging."""
    return tmp_path / "test_attribution.jsonl"


@pytest.fixture
def mock_execution_client() -> MagicMock:
    """Mocked AlpacaExecutionClient fixture."""
    client = MagicMock()
    client.place_take_profit_close_order = AsyncMock()
    return client


@pytest.fixture
def sample_spread() -> MonitoredSpread:
    """Standard Bull Put Spread position fixture."""
    return MonitoredSpread(
        trade_id="trade-bullput-12345",
        underlying="SPY",
        strategy_name="Bull Put Credit Spread",
        regime="HIGH_IV_TRENDING",
        expiration_date="2026-09-18",
        entry_credit=1.50,
        contracts=2,
        short_strike=550.0,
        long_strike=545.0,
        short_symbol="SPY260918P00550000",
        long_symbol="SPY260918P00545000",
        current_dte=25,
        entry_ivr=62.5,
    )


# ------------------------------------------------------------------------------
# 1. PositionMonitorAgent Exit Trigger Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_take_profit_trigger_at_60_percent(
    sample_spread: MonitoredSpread,
    temp_attribution_log: Path,
    mock_execution_client: MagicMock,
) -> None:
    """
    Verifies that spread closes and captures profit when spread market price decays
    to 40% of entry credit (harvesting 60% of credit).
    Entry Credit: $1.50 -> Target Price: $1.50 * 0.40 = $0.60
    """
    logger = AttributionLogger(log_path=temp_attribution_log)
    monitor = PositionMonitorAgent(
        execution_client=mock_execution_client,
        attribution_logger=logger,
        mock_mode=True,
    )

    monitor.track_spread(sample_spread)
    assert len(monitor.get_tracked_spreads()) == 1

    # Simulate price decay to $0.60 (Target reached)
    closed_records = await monitor.evaluate_positions(
        simulated_prices={sample_spread.trade_id: 0.60}
    )

    assert len(closed_records) == 1
    record = closed_records[0]
    assert record.exit_reason == "TAKE_PROFIT_60"
    assert record.exit_price == 0.60
    # Profit = ($1.50 - $0.60) * 100 * 2 contracts = $90.0 * 2 = $180.0
    assert record.realized_pnl == 180.0
    assert record.pnl_pct == 60.0

    # Verify spread was removed from active tracking
    assert len(monitor.get_tracked_spreads()) == 0
    # Verify execution client was invoked to place closing order
    mock_execution_client.place_take_profit_close_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_loss_trigger_at_2_5x_credit(
    sample_spread: MonitoredSpread,
    temp_attribution_log: Path,
    mock_execution_client: MagicMock,
) -> None:
    """
    Verifies that emergency stop triggers when spread market price expands to
    2.5x initial credit received.
    Entry Credit: $1.50 -> Stop Trigger: $1.50 * 2.50 = $3.75
    """
    logger = AttributionLogger(log_path=temp_attribution_log)
    monitor = PositionMonitorAgent(
        execution_client=mock_execution_client,
        attribution_logger=logger,
        mock_mode=True,
    )

    monitor.track_spread(sample_spread)

    # Simulate spread expansion to $3.75 (Stop breached)
    closed_records = await monitor.evaluate_positions(
        simulated_prices={sample_spread.trade_id: 3.75}
    )

    assert len(closed_records) == 1
    record = closed_records[0]
    assert record.exit_reason == "STOP_LOSS_2.5X"
    assert record.exit_price == 3.75
    # Loss = ($1.50 - $3.75) * 100 * 2 contracts = -$225.0 * 2 = -$450.0
    assert record.realized_pnl == -450.0

    assert len(monitor.get_tracked_spreads()) == 0
    mock_execution_client.place_take_profit_close_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_dte_expiry_defense_trigger_at_3_dte(
    sample_spread: MonitoredSpread,
    temp_attribution_log: Path,
    mock_execution_client: MagicMock,
) -> None:
    """
    Verifies that position closes at 3 DTE to eliminate pin risk and gamma acceleration
    even if profit target has not yet been triggered.
    """
    logger = AttributionLogger(log_path=temp_attribution_log)
    monitor = PositionMonitorAgent(
        execution_client=mock_execution_client,
        attribution_logger=logger,
        mock_mode=True,
    )

    # Set DTE to 3
    sample_spread.current_dte = 3
    monitor.track_spread(sample_spread)

    # Current price is $1.00 (between TP $0.60 and SL $3.75)
    closed_records = await monitor.evaluate_positions(
        simulated_prices={sample_spread.trade_id: 1.00}
    )

    assert len(closed_records) == 1
    record = closed_records[0]
    assert record.exit_reason == "DTE_EXPIRY_3D"
    assert record.exit_price == 1.00
    # Profit = ($1.50 - $1.00) * 100 * 2 = $100.0
    assert record.realized_pnl == 100.0

    assert len(monitor.get_tracked_spreads()) == 0


@pytest.mark.asyncio
async def test_position_held_when_no_thresholds_breached(
    sample_spread: MonitoredSpread,
    temp_attribution_log: Path,
    mock_execution_client: MagicMock,
) -> None:
    """Verifies that positions remain open when within normal holding bounds."""
    logger = AttributionLogger(log_path=temp_attribution_log)
    monitor = PositionMonitorAgent(
        execution_client=mock_execution_client,
        attribution_logger=logger,
        mock_mode=True,
    )

    sample_spread.current_dte = 20
    monitor.track_spread(sample_spread)

    # Price at $1.20 (within $0.60 and $3.75, DTE=20)
    closed_records = await monitor.evaluate_positions(
        simulated_prices={sample_spread.trade_id: 1.20}
    )

    assert len(closed_records) == 0
    assert len(monitor.get_tracked_spreads()) == 1
    mock_execution_client.place_take_profit_close_order.assert_not_called()


# ------------------------------------------------------------------------------
# 2. AttributionLogger Analytics & Report Tests
# ------------------------------------------------------------------------------

def test_attribution_logger_summary_and_markdown(temp_attribution_log: Path) -> None:
    """Verifies aggregate performance statistics calculation and presentation formatting."""
    logger = AttributionLogger(log_path=temp_attribution_log)

    # Add winning trades
    logger.record_trade_exit(
        TradeAttributionRecord(
            trade_id="trade-1",
            ticker="SPY",
            strategy_name="Bull Put Credit Spread",
            regime="HIGH_IV_TRENDING",
            entry_date=datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc),
            entry_credit=1.50,
            exit_price=0.60,
            contracts=1,
            realized_pnl=90.0,
            pnl_pct=60.0,
            exit_reason="TAKE_PROFIT_60",
        )
    )
    logger.record_trade_exit(
        TradeAttributionRecord(
            trade_id="trade-2",
            ticker="QQQ",
            strategy_name="Iron Condor",
            regime="HIGH_IV_RANGEBOUND",
            entry_date=datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc),
            entry_credit=2.00,
            exit_price=0.80,
            contracts=1,
            realized_pnl=120.0,
            pnl_pct=60.0,
            exit_reason="TAKE_PROFIT_60",
        )
    )
    # Add losing trade
    logger.record_trade_exit(
        TradeAttributionRecord(
            trade_id="trade-3",
            ticker="IWM",
            strategy_name="Bear Call Credit Spread",
            regime="HIGH_IV_TRENDING",
            entry_date=datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc),
            entry_credit=1.20,
            exit_price=3.00,
            contracts=1,
            realized_pnl=-180.0,
            pnl_pct=-150.0,
            exit_reason="STOP_LOSS_2.5X",
        )
    )

    summary: AttributionSummary = logger.generate_summary()
    assert summary.total_trades == 3
    assert summary.winning_trades == 2
    assert summary.losing_trades == 1
    assert summary.win_rate == 66.7
    # Total PnL = 90 + 120 - 180 = +30.0
    assert summary.total_realized_pnl == 30.0
    # Profit factor: (90 + 120) / 180 = 210 / 180 = 1.17
    assert summary.profit_factor == 1.17
    assert summary.trades_by_exit_reason["TAKE_PROFIT_60"] == 2
    assert summary.trades_by_exit_reason["STOP_LOSS_2.5X"] == 1

    # Verify markdown presentation report formatting
    memo = logger.format_hackathon_presentation_markdown()
    assert "# Quantitative Performance & Risk-Adjusted Alpha Attribution" in memo
    assert "Win Rate" in memo
    assert "TAKE_PROFIT_60" in memo
    assert "HIGH_IV_TRENDING" in memo
