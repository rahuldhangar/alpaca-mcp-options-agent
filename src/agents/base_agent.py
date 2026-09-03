"""
src/agents/base_agent.py
Abstract base agent providing async lifecycle, event bus integration, and telemetry.
"""

from abc import ABC, abstractmethod
import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from src.core.event_bus import EventBus, event_bus as default_event_bus


class AgentTelemetry(BaseModel):
    """Execution statistics and telemetry for autonomous agents."""

    agent_name: str
    is_running: bool = False
    proposals_generated: int = 0
    errors_encountered: int = 0
    last_action_timestamp: Optional[datetime] = None


class BaseAgent(ABC):
    """Abstract asynchronous base agent with event dispatching and lifecycle hooks."""

    def __init__(
        self,
        name: str,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.name: str = name
        self.event_bus: EventBus = event_bus or default_event_bus
        self.logger: logging.Logger = logging.getLogger(f"agent.{name}")
        self.telemetry: AgentTelemetry = AgentTelemetry(agent_name=name)
        self._running: bool = False

    @abstractmethod
    async def start(self) -> None:
        """Starts background tasks or listeners for the agent."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully halts agent execution."""
        pass

    def record_activity(self) -> None:
        """Updates last activity timestamp in telemetry."""
        self.telemetry.last_action_timestamp = datetime.now(timezone.utc)
