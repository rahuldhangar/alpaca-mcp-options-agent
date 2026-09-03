"""
tests/unit/test_event_bus.py
Comprehensive unit tests for the asynchronous Pub/Sub EventBus and typed Pydantic event models.
"""

import asyncio
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from src.core.event_bus import (
    BaseEvent,
    EventBus,
    FillEvent,
    MarketTickEvent,
    OptionsChainSnapshotEvent,
    OrderExecutionEvent,
    OrderProposalEvent,
    SignalEvent,
)


@pytest.fixture
def fresh_bus() -> EventBus:
    """Provides an isolated EventBus instance for each test."""
    return EventBus()


# ------------------------------------------------------------------------------
# 1. Event Model Schema & Validation Tests
# ------------------------------------------------------------------------------

def test_market_tick_event_calculations() -> None:
    """Verifies MarketTickEvent mid-price, spread, and slippage calculations."""
    tick = MarketTickEvent(
        symbol="SPY",
        bid=560.10,
        ask=560.30,
        last_price=560.25,
        volume=1500,
    )
    assert tick.symbol == "SPY"
    assert pytest.approx(tick.mid_price, 0.001) == 560.20
    assert pytest.approx(tick.spread, 0.001) == 0.20
    assert pytest.approx(tick.slippage_pct, 0.0001) == (0.20 / 560.20)
    assert tick.event_type == "MarketTickEvent"
    assert tick.event_id is not None
    assert isinstance(tick.timestamp, datetime)


def test_market_tick_event_validation_errors() -> None:
    """Verifies that negative prices raise ValidationError."""
    with pytest.raises(ValidationError):
        MarketTickEvent(symbol="SPY", bid=-1.0, ask=560.0, last_price=560.0)


def test_options_chain_snapshot_event() -> None:
    """Verifies OptionsChainSnapshotEvent instantiation and attributes."""
    snapshot = OptionsChainSnapshotEvent(
        symbol="QQQ",
        underlying_price=485.50,
        expiration_dates=["2026-09-18", "2026-10-16"],
        strikes=[480.0, 485.0, 490.0],
        contracts_count=12,
        snapshot_data={"QQQ260918C00485000": {"bid": 5.20, "ask": 5.30}},
    )
    assert snapshot.symbol == "QQQ"
    assert snapshot.underlying_price == 485.50
    assert len(snapshot.expiration_dates) == 2
    assert len(snapshot.strikes) == 3
    assert snapshot.event_type == "OptionsChainSnapshotEvent"


def test_signal_event_validation() -> None:
    """Verifies SignalEvent confidence boundaries (0.0 to 1.0)."""
    signal = SignalEvent(
        symbol="SPY",
        regime="HIGH_IV_RANGEBOUND",
        signal_type="IRON_CONDOR",
        confidence=0.85,
        metadata={"ivr": 62.5, "adx": 18.2},
    )
    assert signal.confidence == 0.85
    assert signal.regime == "HIGH_IV_RANGEBOUND"

    # Confidence > 1.0 must fail
    with pytest.raises(ValidationError):
        SignalEvent(
            symbol="SPY",
            regime="HIGH_IV_RANGEBOUND",
            signal_type="IRON_CONDOR",
            confidence=1.5,
        )


def test_order_proposal_event_defaults() -> None:
    """Verifies OrderProposalEvent defaults and risk authorization flag."""
    proposal = OrderProposalEvent(
        symbol="NVDA",
        strategy_name="Bull Put Credit Spread",
        legs=[
            {"side": "sell", "symbol": "NVDA260918P00120000", "delta": -0.25},
            {"side": "buy", "symbol": "NVDA260918P00115000", "delta": -0.10},
        ],
        target_credit=1.25,
        max_profit=125.0,
        max_loss=375.0,
        required_margin=500.0,
        ivr=65.0,
        dte=28,
        thesis="High IV Rank after earnings with strong support at 115.",
        source_model="Gemini-3.8-Flash",
    )
    assert proposal.is_approved is False
    assert proposal.max_loss == 375.0
    assert proposal.proposal_id is not None
    assert proposal.event_type == "OrderProposalEvent"


def test_order_execution_and_fill_events() -> None:
    """Verifies OrderExecutionEvent and FillEvent schemas."""
    exec_event = OrderExecutionEvent(
        order_id="ord-12345",
        client_order_id="cli-67890",
        symbol="SPY",
        status="ACCEPTED",
        order_type="mleg",
        limit_price=1.20,
    )
    assert exec_event.order_id == "ord-12345"
    assert exec_event.status == "ACCEPTED"

    fill = FillEvent(
        order_id="ord-12345",
        client_order_id="cli-67890",
        symbol="SPY",
        fill_price=1.22,
        quantity=5,
        execution_fees=0.35,
    )
    assert fill.quantity == 5
    assert fill.fill_price == 1.22
    assert fill.execution_fees == 0.35
    assert fill.event_type == "FillEvent"


# ------------------------------------------------------------------------------
# 2. Pub/Sub Dispatch & Error Isolation Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_and_async_subscriber_dispatch(fresh_bus: EventBus) -> None:
    """Verifies that both synchronous and asynchronous callbacks receive dispatched events."""
    received_sync: list[MarketTickEvent] = []
    received_async: list[MarketTickEvent] = []

    def sync_handler(event: MarketTickEvent) -> None:
        received_sync.append(event)

    async def async_handler(event: MarketTickEvent) -> None:
        await asyncio.sleep(0.01)
        received_async.append(event)

    fresh_bus.subscribe(MarketTickEvent, sync_handler)
    fresh_bus.subscribe(MarketTickEvent, async_handler)

    tick = MarketTickEvent(symbol="SPY", bid=560.0, ask=560.10, last_price=560.05)
    await fresh_bus.dispatch(tick)

    assert len(received_sync) == 1
    assert len(received_async) == 1
    assert received_sync[0].symbol == "SPY"
    assert received_async[0].symbol == "SPY"
    assert fresh_bus.events_delivered == 2


@pytest.mark.asyncio
async def test_topic_isolation(fresh_bus: EventBus) -> None:
    """Verifies that subscribers only receive events of the subscribed type."""
    signals: list[SignalEvent] = []
    fills: list[FillEvent] = []

    fresh_bus.subscribe(SignalEvent, lambda e: signals.append(e))
    fresh_bus.subscribe(FillEvent, lambda e: fills.append(e))

    sig = SignalEvent(
        symbol="QQQ",
        regime="LOW_IV_TRENDING",
        signal_type="DEBIT_SPREAD",
        confidence=0.75,
    )
    await fresh_bus.dispatch(sig)

    assert len(signals) == 1
    assert len(fills) == 0  # Fill subscriber must not have received SignalEvent


@pytest.mark.asyncio
async def test_error_isolation_between_subscribers(fresh_bus: EventBus) -> None:
    """
    CRITICAL RISK TEST: A crashing handler must NOT crash other subscribers
    or prevent them from receiving the event.
    """
    successful_calls: list[str] = []

    def failing_handler(event: BaseEvent) -> None:
        raise RuntimeError("Simulated crash in rogue subscriber!")

    async def healthy_handler(event: BaseEvent) -> None:
        successful_calls.append("healthy_executed")

    fresh_bus.subscribe(MarketTickEvent, failing_handler)
    fresh_bus.subscribe(MarketTickEvent, healthy_handler)

    tick = MarketTickEvent(symbol="IWM", bid=220.0, ask=220.05, last_price=220.02)
    # Must not raise an unhandled exception
    await fresh_bus.dispatch(tick)

    assert len(successful_calls) == 1
    assert successful_calls[0] == "healthy_executed"
    assert fresh_bus.errors_caught == 1
    assert fresh_bus.events_delivered == 1


@pytest.mark.asyncio
async def test_unsubscribe_functionality(fresh_bus: EventBus) -> None:
    """Verifies that an unsubscribed handler is no longer invoked."""
    calls: list[str] = []

    def handler(event: BaseEvent) -> None:
        calls.append("called")

    fresh_bus.subscribe(MarketTickEvent, handler)
    tick = MarketTickEvent(symbol="AAPL", bid=225.0, ask=225.10, last_price=225.05)

    await fresh_bus.dispatch(tick)
    assert len(calls) == 1

    fresh_bus.unsubscribe(MarketTickEvent, handler)
    await fresh_bus.dispatch(tick)
    assert len(calls) == 1  # Still 1, not called again


@pytest.mark.asyncio
async def test_decorator_registration(fresh_bus: EventBus) -> None:
    """Verifies the @event_bus.on decorator syntax."""
    events_received: list[str] = []

    @fresh_bus.on(MarketTickEvent)
    def on_tick(e: MarketTickEvent) -> None:
        events_received.append(e.symbol)

    tick = MarketTickEvent(symbol="MSFT", bid=440.0, ask=440.20, last_price=440.10)
    await fresh_bus.dispatch(tick)

    assert events_received == ["MSFT"]


# ------------------------------------------------------------------------------
# 3. Queue Lifecycle & Background Worker Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_queue_background_worker_lifecycle(fresh_bus: EventBus) -> None:
    """Verifies async background queue publication, consumption, and graceful shutdown."""
    consumed_events: list[str] = []

    async def async_consumer(event: MarketTickEvent) -> None:
        consumed_events.append(event.symbol)

    fresh_bus.subscribe(MarketTickEvent, async_consumer)

    # Start the worker
    await fresh_bus.start()

    # Publish multiple events asynchronously
    symbols = ["SPY", "QQQ", "IWM", "TSLA", "NVDA"]
    for sym in symbols:
        await fresh_bus.publish(MarketTickEvent(symbol=sym, bid=100.0, ask=100.05, last_price=100.02))

    # Wait until all queued items are processed
    await fresh_bus.wait_until_idle()

    assert consumed_events == symbols
    assert fresh_bus.events_published == 5
    assert fresh_bus.events_delivered == 5

    # Stop the worker gracefully
    await fresh_bus.stop()
    assert fresh_bus._is_running is False
