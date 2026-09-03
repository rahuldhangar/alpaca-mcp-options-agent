"""
src/core/config.py
Pydantic v2 application configuration supporting dual-account Alpaca routing,
multi-model LLM provider switching (Gemini vs. Featherless), and deterministic
Aggressive Hackathon Tier hard risk boundaries.
"""

from typing import List, Literal, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Master configuration settings loaded from environment variables and .env file.
    Follows Pydantic v2 BaseSettings specification.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --------------------------------------------------------------------------
    # 1. Dual-Account Alpaca Configuration
    # --------------------------------------------------------------------------
    ACTIVE_ACCOUNT: Literal["test", "competition"] = Field(
        default="test",
        description="Active account to route orders: 'test' (dry-runs) or 'competition' ($100k official scoring)",
    )

    # General / Default Alpaca Credentials (fallback)
    ALPACA_API_KEY: Optional[str] = Field(
        default=None,
        description="Default Alpaca API Key",
    )
    ALPACA_SECRET_KEY: Optional[str] = Field(
        default=None,
        description="Default Alpaca Secret Key",
    )

    # Testing Paper Account Credentials
    ALPACA_TEST_API_KEY: Optional[str] = Field(
        default=None,
        description="Testing Paper Account API Key",
    )
    ALPACA_TEST_SECRET_KEY: Optional[str] = Field(
        default=None,
        description="Testing Paper Account Secret Key",
    )

    # Official Competition Paper Account Credentials ($100,000 initial equity)
    ALPACA_COMPETITION_API_KEY: Optional[str] = Field(
        default=None,
        description="Official Competition Account API Key ($100,000)",
    )
    ALPACA_COMPETITION_SECRET_KEY: Optional[str] = Field(
        default=None,
        description="Official Competition Account Secret Key",
    )

    # Alpaca Endpoints
    ALPACA_PAPER: bool = Field(
        default=True,
        description="True for paper trading sandbox, False for live money",
    )
    ALPACA_BASE_URL: str = Field(
        default="https://paper-api.alpaca.markets",
        description="REST API base URL for orders and account management",
    )
    ALPACA_DATA_URL: str = Field(
        default="https://data.alpaca.markets",
        description="Market data REST and WebSocket stream URL",
    )

    # --------------------------------------------------------------------------
    # 2. Multi-Model LLM Strategist Configuration
    # --------------------------------------------------------------------------
    LLM_PROVIDER: Literal["gemini", "featherless"] = Field(
        default="gemini",
        description="Active LLM intelligence engine during automated trading: 'gemini' or 'featherless'",
    )

    # Google Gemini Settings
    GEMINI_API_KEY: Optional[str] = Field(
        default=None,
        description="Google Gemini API Key for autonomous trading decisions",
    )
    GEMINI_MODEL: str = Field(
        default="gemini-2.0-flash",
        description="Gemini model identifier",
    )

    # Featherless AI Settings (OpenAI-Compatible Endpoint)
    FEATHERLESS_API_KEY: Optional[str] = Field(
        default=None,
        description="Featherless API Key (Bearer token, starts with fw-)",
    )
    FEATHERLESS_BASE_URL: str = Field(
        default="https://api.featherless.ai/v1",
        description="OpenAI-compatible base URL for Featherless serverless inference",
    )
    FEATHERLESS_MODEL: str = Field(
        default="Qwen/Qwen2.5-72B-Instruct",
        description="Open-source model slug hosted on Featherless",
    )

    # --------------------------------------------------------------------------
    # 3. Deterministic Hard Risk Gate Parameters (Aggressive Hackathon Tier)
    # --------------------------------------------------------------------------
    INITIAL_CAPITAL: float = Field(
        default=100000.0,
        description="Dedicated official paper account starting capital ($100,000 USD)",
    )
    MAX_RISK_PER_TRADE_PCT: float = Field(
        default=0.05,
        description="Maximum capital at risk per single trade position (5.0% = $5,000 max)",
    )
    MAX_MARGIN_UTILIZATION_PCT: float = Field(
        default=0.40,
        description="Maximum portfolio margin utilization ceiling (40.0% = $40,000 max)",
    )
    DAILY_LOSS_CIRCUIT_BREAKER_PCT: float = Field(
        default=0.05,
        description="Intraday equity drawdown circuit breaker threshold (5.0% = $5,000 max)",
    )
    MAX_PORTFOLIO_DRAWDOWN_PCT: float = Field(
        default=0.10,
        description="Absolute peak-to-trough portfolio drawdown stop (10.0% = $10,000 max)",
    )
    TAKE_PROFIT_PCT: float = Field(
        default=0.60,
        description="Automated profit harvesting target (60% of maximum potential credit collected)",
    )
    STOP_LOSS_MULTIPLIER: float = Field(
        default=2.5,
        description="Automated hard stop-loss trigger (2.5x initial credit collected)",
    )
    MAX_SLIPPAGE_PCT: float = Field(
        default=0.03,
        description="Maximum allowable bid-ask spread slippage (3.0% of midpoint)",
    )
    MAX_SLIPPAGE_DOLLARS: float = Field(
        default=0.15,
        description="Maximum allowable absolute bid-ask spread in dollars per contract ($0.15)",
    )

    # DTE Boundaries
    PRIMARY_DTE_MIN: int = Field(default=14, description="Primary options expiration minimum DTE")
    PRIMARY_DTE_MAX: int = Field(default=45, description="Primary options expiration maximum DTE")
    TACTICAL_DTE_MIN: int = Field(default=0, description="Tactical options expiration minimum DTE")
    TACTICAL_DTE_MAX: int = Field(default=7, description="Tactical options expiration maximum DTE")

    # --------------------------------------------------------------------------
    # 4. System & Whitelist Settings
    # --------------------------------------------------------------------------
    LOG_LEVEL: str = Field(default="INFO", description="Console and file logging severity")
    TICKER_WHITELIST: List[str] = Field(
        default=[
            "SPY",
            "QQQ",
            "IWM",
            "NVDA",
            "AAPL",
            "MSFT",
            "TSLA",
            "AMZN",
            "GOOGL",
            "META",
        ],
        description="Active whitelist of liquid underlying instruments",
    )

    # --------------------------------------------------------------------------
    # Dynamic Credential Properties
    # --------------------------------------------------------------------------
    @property
    def api_key(self) -> str:
        """
        Dynamically returns the Alpaca API key corresponding to the ACTIVE_ACCOUNT.
        Falls back to ALPACA_API_KEY if specific account key is not defined.
        """
        if self.ACTIVE_ACCOUNT == "competition":
            key = self.ALPACA_COMPETITION_API_KEY or self.ALPACA_API_KEY
        else:
            key = self.ALPACA_TEST_API_KEY or self.ALPACA_API_KEY
        return key or ""

    @property
    def secret_key(self) -> str:
        """
        Dynamically returns the Alpaca Secret key corresponding to the ACTIVE_ACCOUNT.
        Falls back to ALPACA_SECRET_KEY if specific account key is not defined.
        """
        if self.ACTIVE_ACCOUNT == "competition":
            secret = self.ALPACA_COMPETITION_SECRET_KEY or self.ALPACA_SECRET_KEY
        else:
            secret = self.ALPACA_TEST_SECRET_KEY or self.ALPACA_SECRET_KEY
        return secret or ""

    @property
    def is_competition(self) -> bool:
        """Returns True if the system is currently routed to the official $100k competition account."""
        return self.ACTIVE_ACCOUNT == "competition"

    # Dollar Boundary Calculations
    @property
    def max_risk_per_trade_dollars(self) -> float:
        """Calculates absolute dollar risk boundary based on initial capital ($5,000.00)."""
        return self.INITIAL_CAPITAL * self.MAX_RISK_PER_TRADE_PCT

    @property
    def max_margin_dollars(self) -> float:
        """Calculates absolute dollar margin utilization ceiling ($40,000.00)."""
        return self.INITIAL_CAPITAL * self.MAX_MARGIN_UTILIZATION_PCT

    @property
    def daily_loss_breaker_dollars(self) -> float:
        """Calculates absolute daily loss circuit breaker threshold ($5,000.00)."""
        return self.INITIAL_CAPITAL * self.DAILY_LOSS_CIRCUIT_BREAKER_PCT

    @property
    def max_drawdown_dollars(self) -> float:
        """Calculates absolute peak-to-trough emergency stop drawdown threshold ($10,000.00)."""
        return self.INITIAL_CAPITAL * self.MAX_PORTFOLIO_DRAWDOWN_PCT


# Singleton global instance
settings: Settings = Settings()
