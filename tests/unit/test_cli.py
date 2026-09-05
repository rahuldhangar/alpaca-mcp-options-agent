"""
tests/unit/test_cli.py
Unit tests for the Turnkey Typer/Rich CLI entrypoint (src/cli/main.py).
Validates commands, account switching, provider flags, and interactive outputs.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
import pytest
from typer.testing import CliRunner

from src.cli.main import app
from src.core.config import settings
from src.execution.alpaca_client import MarketClockState
from src.risk.portfolio_state import PortfolioState

runner = CliRunner()


def test_cli_help_displays_all_commands() -> None:
    """Verifies that CLI top-level help lists all four institutional commands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run-paper" in result.output
    assert "test-risk-gate" in result.output
    assert "inspect-account" in result.output
    assert "attribution-report" in result.output


def test_cli_test_risk_gate_execution() -> None:
    """Verifies that test-risk-gate runs and evaluates boundary conditions."""
    result = runner.invoke(app, ["test-risk-gate"])
    assert result.exit_code == 0
    assert "Risk Gate Boundary Test Results" in result.output
    assert "APPROVED" in result.output
    assert "REJECTED" in result.output
    assert "All deterministic boundary gate checks executed successfully!" in result.output


@patch("src.execution.alpaca_client.AlpacaExecutionClient.get_portfolio_state")
def test_cli_inspect_account(mock_get_portfolio: AsyncMock) -> None:
    """Verifies that inspect-account outputs the Total Account Equity table."""
    mock_get_portfolio.return_value = PortfolioState(
        equity=100000.0,
        cash=100000.0,
        buying_power=100000.0,
        day_starting_equity=100000.0,
        peak_equity=100000.0,
    )

    result = runner.invoke(app, ["inspect-account", "--account", "test"])
    assert result.exit_code == 0
    assert "Total Account Equity" in result.output
    assert "$100,000.00" in result.output
    assert "PRIMARY HACKATHON SCORING METRIC" in result.output


def test_cli_attribution_report_empty_state() -> None:
    """Verifies attribution-report handles empty logs gracefully."""
    result = runner.invoke(app, ["attribution-report"])
    assert result.exit_code == 0
    # Either reports no records or formats table if records exist
    assert "No closed trades recorded yet" in result.output or "Hackathon Alpha Attribution Summary" in result.output


def test_cli_run_paper_mock_cycles() -> None:
    """Verifies run-paper executes in mock simulation mode for fixed cycles."""
    result = runner.invoke(
        app,
        [
            "run-paper",
            "--account", "test",
            "--llm-provider", "featherless",
            "--model", "Qwen/Qwen2.5-72B-Instruct",
            "--mock",
            "--cycles", "1",
        ],
    )
    assert result.exit_code == 0
    assert "Initializing Autonomous Paper Trading Agent" in result.output
    assert "EQUITY" in result.output
    assert "FEATHERLESS" in result.output


@patch("src.agents.strategist_agent.StrategistAgent.formulate_strategy", new_callable=AsyncMock)
@patch("src.execution.alpaca_client.AlpacaExecutionClient.get_market_clock")
@patch("src.execution.alpaca_client.AlpacaExecutionClient.get_portfolio_state")
def test_cli_run_paper_market_closed_standby(
    mock_get_portfolio: AsyncMock,
    mock_get_clock: AsyncMock,
    mock_formulate_strategy: AsyncMock,
) -> None:
    """Verifies that run-paper enters Standby mode when market is closed and skips LLM strategy calls."""
    now = datetime(2026, 9, 5, 20, 0, 0, tzinfo=timezone.utc)
    mock_get_portfolio.return_value = PortfolioState(
        equity=100000.0,
        cash=100000.0,
        buying_power=100000.0,
        day_starting_equity=100000.0,
        peak_equity=100000.0,
    )
    mock_get_clock.return_value = MarketClockState(
        is_open=False,
        next_open=now + timedelta(days=2, hours=13, minutes=30),
        next_close=now + timedelta(days=2, hours=20),
        timestamp=now,
    )

    result = runner.invoke(
        app,
        [
            "run-paper",
            "--account", "test",
            "--cycles", "1",
        ],
    )
    assert result.exit_code == 0
    # LLM formulate_strategy must NEVER be called when market is closed
    mock_formulate_strategy.assert_not_called()
    assert "STANDBY" in result.output or "MARKET CLOSED" in result.output


@patch("src.agents.strategist_agent.StrategistAgent.formulate_strategy", new_callable=AsyncMock)
@patch("src.execution.alpaca_client.AlpacaExecutionClient.get_market_clock")
@patch("src.execution.alpaca_client.AlpacaExecutionClient.get_portfolio_state")
def test_cli_run_paper_force_eval_overrides_market_closed(
    mock_get_portfolio: AsyncMock,
    mock_get_clock: AsyncMock,
    mock_formulate_strategy: AsyncMock,
) -> None:
    """Verifies that --force-eval executes strategy formulation even when market is closed."""
    now = datetime(2026, 9, 5, 20, 0, 0, tzinfo=timezone.utc)
    mock_get_portfolio.return_value = PortfolioState(
        equity=100000.0,
        cash=100000.0,
        buying_power=100000.0,
        day_starting_equity=100000.0,
        peak_equity=100000.0,
    )
    mock_get_clock.return_value = MarketClockState(
        is_open=False,
        next_open=now + timedelta(days=2, hours=13, minutes=30),
        next_close=now + timedelta(days=2, hours=20),
        timestamp=now,
    )
    mock_formulate_strategy.return_value = None

    result = runner.invoke(
        app,
        [
            "run-paper",
            "--account", "test",
            "--cycles", "1",
            "--force-eval",
        ],
    )
    assert result.exit_code == 0
    # formulate_strategy should be called for top candidates because --force-eval overrides closed market
    assert mock_formulate_strategy.called
    assert "FORCE-EVAL ACTIVE" in result.output

