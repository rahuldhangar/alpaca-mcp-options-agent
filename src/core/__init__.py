"""Core system configuration, events, and exceptions."""

from src.core.config import Settings, settings
from src.core.exceptions import (
    TradingSystemError,
    RiskGateViolationError,
    OCCFormattingError,
    SlippageLimitExceededError,
    AlpacaAPIError,
    LLMProviderError,
    CircuitBreakerTriggeredError,
)
from src.core.event_bus import (
    BaseEvent,
    MarketTickEvent,
    OptionsChainSnapshotEvent,
    SignalEvent,
    OrderProposalEvent,
    OrderExecutionEvent,
    FillEvent,
    EventBus,
    event_bus,
)

__all__ = [
    "Settings",
    "settings",
    "TradingSystemError",
    "RiskGateViolationError",
    "OCCFormattingError",
    "SlippageLimitExceededError",
    "AlpacaAPIError",
    "LLMProviderError",
    "CircuitBreakerTriggeredError",
    "BaseEvent",
    "MarketTickEvent",
    "OptionsChainSnapshotEvent",
    "SignalEvent",
    "OrderProposalEvent",
    "OrderExecutionEvent",
    "FillEvent",
    "EventBus",
    "event_bus",
]
