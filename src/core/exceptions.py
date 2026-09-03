"""
src/core/exceptions.py
Custom strongly-typed exceptions for the Alpaca autonomous options trading system.
"""

from typing import Optional


class TradingSystemError(Exception):
    """Base exception class for all autonomous trading system errors."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message: str = message

    def __str__(self) -> str:
        return f"[{self.__class__.__name__}] {self.message}"


class RiskGateViolationError(TradingSystemError):
    """Raised when an options order or portfolio state violates deterministic hard risk gates."""

    def __init__(
        self,
        violation_type: Optional[str] = None,
        current_value: Optional[float] = None,
        limit_value: Optional[float] = None,
        details: Optional[str] = None,
        rule_name: Optional[str] = None,
        reason: Optional[str] = None,
        threshold_value: Optional[float] = None,
    ) -> None:
        self.violation_type: str = violation_type or rule_name or "UNKNOWN_RULE"
        self.current_value: Optional[float] = current_value
        self.limit_value: Optional[float] = limit_value if limit_value is not None else threshold_value
        self.details: Optional[str] = details or reason

        val_str = f"{self.current_value:.4f}" if self.current_value is not None else "N/A"
        lim_str = f"{self.limit_value:.4f}" if self.limit_value is not None else "N/A"
        msg = f"Risk boundary breached: {self.violation_type} (Current: {val_str}, Limit: {lim_str})"
        if self.details:
            msg += f" - {self.details}"
        super().__init__(msg)


class OrderExecutionError(TradingSystemError):
    """Raised when an order fails during validation, submission, or fill processing."""

    def __init__(self, order_id: str, reason: str) -> None:
        self.order_id: str = order_id
        self.reason: str = reason
        super().__init__(f"Order execution error for '{order_id}': {reason}")


class OCCFormattingError(TradingSystemError):
    """Raised when an options contract symbol fails strict 21-character OCC formatting standards."""

    def __init__(self, symbol: str, reason: str) -> None:
        self.symbol: str = symbol
        self.reason: str = reason
        super().__init__(f"Invalid OCC options symbol '{symbol}': {reason}")


class SlippageLimitExceededError(TradingSystemError):
    """Raised when bid-ask spread exceeds maximum allowable percentage or dollar threshold."""

    def __init__(
        self,
        symbol: str,
        bid: float,
        ask: float,
        spread: float,
        max_allowed_spread: float,
    ) -> None:
        self.symbol: str = symbol
        self.bid: float = bid
        self.ask: float = ask
        self.spread: float = spread
        self.max_allowed_spread: float = max_allowed_spread
        super().__init__(
            f"Slippage limit exceeded for {symbol}: Bid={bid:.2f}, Ask={ask:.2f}, "
            f"Spread={spread:.4f}, MaxAllowed={max_allowed_spread:.4f}"
        )


class AlpacaAPIError(TradingSystemError):
    """Raised when an Alpaca Trading API or Market Data API request fails."""

    def __init__(
        self,
        error_message: str,
        status_code: Optional[int] = None,
        endpoint: Optional[str] = None,
    ) -> None:
        self.error_message: str = error_message
        self.status_code: Optional[int] = status_code
        self.endpoint: Optional[str] = endpoint
        msg = f"Alpaca API error: {error_message}"
        if status_code is not None:
            msg += f" (HTTP {status_code})"
        if endpoint is not None:
            msg += f" on endpoint '{endpoint}'"
        super().__init__(msg)


class LLMProviderError(TradingSystemError):
    """Raised when an LLM provider (Gemini or Featherless) fails to return a valid response."""

    def __init__(
        self,
        provider: str,
        model: str,
        error_message: str,
        status_code: Optional[int] = None,
    ) -> None:
        self.provider: str = provider
        self.model: str = model
        self.error_message: str = error_message
        self.status_code: Optional[int] = status_code
        msg = f"LLM provider '{provider}' error with model '{model}': {error_message}"
        if status_code is not None:
            msg += f" (HTTP {status_code})"
        super().__init__(msg)


class CircuitBreakerTriggeredError(TradingSystemError):
    """Raised when a daily loss or total portfolio drawdown circuit breaker trips."""

    def __init__(
        self,
        breaker_type: str,
        current_loss: float,
        threshold: float,
        action_taken: str = "Trading halted, open orders canceled.",
    ) -> None:
        self.breaker_type: str = breaker_type
        self.current_loss: float = current_loss
        self.threshold: float = threshold
        self.action_taken: str = action_taken
        super().__init__(
            f"CIRCUIT BREAKER TRIPPED [{breaker_type}]: Loss={current_loss:.2f}, "
            f"Threshold={threshold:.2f}. Action: {action_taken}"
        )
