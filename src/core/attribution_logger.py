"""
src/core/attribution_logger.py
Performance attribution logger recording position exits, PnL, and regime analytics.
Produces institutional attribution summaries suitable for hackathon presentation slides.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

logger = logging.getLogger("core.attribution")


class TradeAttributionRecord(BaseModel):
    """Immutable audit record generated upon position exit."""

    trade_id: str = Field(description="Unique trade tracking UUID")
    ticker: str = Field(description="Underlying ticker symbol (e.g. SPY)")
    strategy_name: str = Field(description="Options strategy structure")
    regime: str = Field(description="Market regime at trade entry")
    entry_date: datetime = Field(description="UTC timestamp of trade opening")
    exit_date: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of trade closure",
    )
    entry_credit: float = Field(description="Net credit collected per spread ($)")
    exit_price: float = Field(description="Cost to close spread ($)")
    contracts: int = Field(default=1, gt=0, description="Number of contracts")
    realized_pnl: float = Field(description="Total realized dollar profit/loss ($)")
    pnl_pct: float = Field(description="Percentage return relative to initial credit or risk")
    exit_reason: str = Field(
        description="Trigger reason: TAKE_PROFIT_60, STOP_LOSS_2.5X, DTE_EXPIRY_3D, CIRCUIT_BREAKER",
    )
    entry_ivr: Optional[float] = Field(default=None, description="IV Rank at entry")
    entry_greeks: Optional[Dict[str, float]] = Field(default=None, description="Greeks at entry")


class AttributionSummary(BaseModel):
    """Aggregate portfolio attribution statistics across competition trading window."""

    total_trades: int = Field(default=0, ge=0)
    winning_trades: int = Field(default=0, ge=0)
    losing_trades: int = Field(default=0, ge=0)
    win_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="Win rate percentage (0-100%)")
    total_realized_pnl: float = Field(default=0.0, description="Net realized dollar P&L ($)")
    profit_factor: float = Field(default=0.0, ge=0.0, description="Gross profit / Gross loss")
    avg_trade_pnl: float = Field(default=0.0, description="Mean dollar P&L per trade ($)")
    trades_by_regime: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    trades_by_exit_reason: Dict[str, int] = Field(default_factory=dict)


class AttributionLogger:
    """
    Structured trade attribution manager logging closed trades to JSONL
    and compiling quantitative analytics for presentation reports.
    """

    def __init__(self, log_path: Optional[Union[str, Path]] = None) -> None:
        self.log_path: Path = Path(log_path or "data/attribution/trade_attribution.jsonl")
        self._records: List[TradeAttributionRecord] = []
        self._ensure_log_dir()
        self._load_existing_records()

    def _ensure_log_dir(self) -> None:
        """Creates attribution log directory if non-existent."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_existing_records(self) -> None:
        """Loads prior trade records from disk if the file exists."""
        if not self.log_path.exists():
            return

        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        record_dict = json.loads(line)
                        self._records.append(TradeAttributionRecord(**record_dict))
            logger.info("Loaded %d historical attribution records.", len(self._records))
        except Exception as exc:
            logger.warning("Error reading attribution log file: %s", exc)

    def record_trade_exit(self, record: TradeAttributionRecord) -> None:
        """Appends a closed trade record to the in-memory store and JSONL log."""
        self._records.append(record)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(record.model_dump_json() + "\n")
            logger.info(
                "Attribution recorded: %s on %s | PnL: $%.2f (%.1f%%) | Reason: %s",
                record.strategy_name,
                record.ticker,
                record.realized_pnl,
                record.pnl_pct,
                record.exit_reason,
            )
        except Exception as exc:
            logger.error("Failed to append attribution record to file: %s", exc)

    def get_all_records(self) -> List[TradeAttributionRecord]:
        """Returns all recorded trade attributions."""
        return list(self._records)

    def generate_summary(self) -> AttributionSummary:
        """Calculates comprehensive portfolio metrics from recorded trade exits."""
        if not self._records:
            return AttributionSummary()

        total_trades = len(self._records)
        winning_trades = sum(1 for r in self._records if r.realized_pnl > 0)
        losing_trades = sum(1 for r in self._records if r.realized_pnl <= 0)
        win_rate = (winning_trades / total_trades) * 100.0 if total_trades > 0 else 0.0

        total_pnl = sum(r.realized_pnl for r in self._records)
        gross_profit = sum(r.realized_pnl for r in self._records if r.realized_pnl > 0)
        gross_loss = abs(sum(r.realized_pnl for r in self._records if r.realized_pnl < 0))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.9 if gross_profit > 0 else 1.0)
        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0.0

        # Grouping by regime
        regimes: Dict[str, Dict[str, Any]] = {}
        for r in self._records:
            reg = r.regime
            if reg not in regimes:
                regimes[reg] = {"trades": 0, "pnl": 0.0, "wins": 0}
            regimes[reg]["trades"] += 1
            regimes[reg]["pnl"] += r.realized_pnl
            if r.realized_pnl > 0:
                regimes[reg]["wins"] += 1

        for reg_data in regimes.values():
            reg_data["pnl"] = round(reg_data["pnl"], 2)
            reg_data["win_rate"] = round((reg_data["wins"] / reg_data["trades"]) * 100.0, 1)

        # Grouping by exit reason
        exit_reasons: Dict[str, int] = {}
        for r in self._records:
            exit_reasons[r.exit_reason] = exit_reasons.get(r.exit_reason, 0) + 1

        return AttributionSummary(
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=round(win_rate, 1),
            total_realized_pnl=round(total_pnl, 2),
            profit_factor=round(profit_factor, 2),
            avg_trade_pnl=round(avg_pnl, 2),
            trades_by_regime=regimes,
            trades_by_exit_reason=exit_reasons,
        )

    def format_hackathon_presentation_markdown(self) -> str:
        """Formats quantitative performance analytics into an institutional markdown memo."""
        summary = self.generate_summary()
        lines = [
            "# Quantitative Performance & Risk-Adjusted Alpha Attribution",
            f"*Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}*",
            "",
            "## 1. Key Performance Indicators (Scoring Dimension 1: P&L Performance)",
            "",
            "| Metric | Result | Target Benchmark | Status |",
            "| :--- | :---: | :---: | :---: |",
            f"| **Total Closed Trades** | `{summary.total_trades}` | $\\ge 10$ | {'PASS' if summary.total_trades >= 10 else 'ACTIVE'} |",
            f"| **Win Rate** | `{summary.win_rate:.1f}%` | $\\ge 70.0\\%$ | {'PASS' if summary.win_rate >= 70 else 'EVALUATING'} |",
            f"| **Net Realized Alpha** | `+${summary.total_realized_pnl:,.2f}` | $> $0.00 | {'ALPHA' if summary.total_realized_pnl > 0 else 'DEFENDING'} |",
            f"| **Profit Factor** | `{summary.profit_factor:.2f}` | $\\ge 2.00$ | {'EXEMPLARY' if summary.profit_factor >= 2.0 else 'OPTIMIZING'} |",
            f"| **Avg Profit Per Trade** | `+${summary.avg_trade_pnl:.2f}` | $> $0.00 | {'PASS' if summary.avg_trade_pnl > 0 else 'MONITOR'} |",
            "",
            "## 2. Regime-Adaptive Alpha Breakdown",
            "",
            "| Market Regime | Trade Count | Win Rate | Total Realized PnL |",
            "| :--- | :---: | :---: | :---: |",
        ]

        for reg, data in summary.trades_by_regime.items():
            lines.append(f"| **{reg}** | `{data['trades']}` | `{data['win_rate']}%` | `+${data['pnl']:,.2f}` |")

        lines.extend([
            "",
            "## 3. Exit Mechanics & Capital Protection Discipline",
            "",
            "| Exit Trigger | Executions | Rationale |",
            "| :--- | :---: | :--- |",
            f"| **60% Take-Profit (`TAKE_PROFIT_60`)** | `{summary.trades_by_exit_reason.get('TAKE_PROFIT_60', 0)}` | Captured optimal theta decay curve |",
            f"| **2.5x Hard Stop-Loss (`STOP_LOSS_2.5X`)** | `{summary.trades_by_exit_reason.get('STOP_LOSS_2.5X', 0)}` | Hard capital defense intercept |",
            f"| **3 DTE Expiry Defense (`DTE_EXPIRY_3D`)** | `{summary.trades_by_exit_reason.get('DTE_EXPIRY_3D', 0)}` | Eliminated gamma & pin risk |",
            f"| **Circuit Breakers (`CIRCUIT_BREAKER`)** | `{summary.trades_by_exit_reason.get('CIRCUIT_BREAKER', 0)}` | Macro portfolio halt |",
        ])

        return "\n".join(lines)
