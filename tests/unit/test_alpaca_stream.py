"""
tests/unit/test_alpaca_stream.py
Unit tests for AlpacaStreamClient and real-time options quote / snapshot ingestion.
"""

import asyncio
import pytest
from src.core.event_bus import EventBus, MarketTickEvent, OptionsChainSnapshotEvent
from src.data.alpaca_stream import AlpacaStreamClient


@pytest.fixture
def mock_stream_client() -> AlpacaStreamClient:
    """Returns an isolated AlpacaStreamClient in mock mode."""
    bus = EventBus()
    return AlpacaStreamClient(
        api_key="TEST_KEY",
        secret_key="TEST_SECRET",
        event_bus=bus,
        mock_mode=True,
    )


@pytest.mark.asyncio
async def test_stream_client_initialization(mock_stream_client: AlpacaStreamClient) -> None:
    """Verifies stream client initial properties and whitelist."""
    assert mock_stream_client.mock_mode is True
    assert "SPY" in mock_stream_client.whitelist
    assert "QQQ" in mock_stream_client.whitelist
    assert "NVDA" in mock_stream_client.whitelist


@pytest.mark.asyncio
async def test_fetch_underlying_quote_mock(mock_stream_client: AlpacaStreamClient) -> None:
    """Verifies fetching underlying quote in mock mode emits valid MarketTickEvent."""
    received_ticks: list[MarketTickEvent] = []
    mock_stream_client.event_bus.subscribe(MarketTickEvent, lambda e: received_ticks.append(e))

    tick = await mock_stream_client.fetch_underlying_quote("SPY")

    assert tick.symbol == "SPY"
    assert tick.bid > 0.0
    assert tick.ask >= tick.bid
    assert tick.last_price > 0.0
    assert len(received_ticks) == 1
    assert received_ticks[0].symbol == "SPY"


@pytest.mark.asyncio
async def test_fetch_options_chain_snapshot_mock(mock_stream_client: AlpacaStreamClient) -> None:
    """Verifies that options chain snapshot generates valid strikes, contracts, and OCC symbols."""
    received_snapshots: list[OptionsChainSnapshotEvent] = []
    mock_stream_client.event_bus.subscribe(OptionsChainSnapshotEvent, lambda e: received_snapshots.append(e))

    snapshot = await mock_stream_client.fetch_options_chain_snapshot("SPY", dte_min=14, dte_max=45)

    assert snapshot.symbol == "SPY"
    assert snapshot.underlying_price > 0.0
    assert len(snapshot.strikes) > 0
    assert snapshot.contracts_count > 0
    assert len(received_snapshots) == 1

    # Check contracts mapping
    first_symbol = list(snapshot.snapshot_data.keys())[0]
    first_data = snapshot.snapshot_data[first_symbol]
    assert "bid" in first_data
    assert "ask" in first_data
    assert "implied_volatility" in first_data


@pytest.mark.asyncio
async def test_run_mock_stream_delivery(mock_stream_client: AlpacaStreamClient) -> None:
    """Verifies mock streaming emits multiple ticks onto the event bus."""
    ticks: list[MarketTickEvent] = []
    mock_stream_client.event_bus.subscribe(MarketTickEvent, lambda e: ticks.append(e))

    # Start event bus background consumer
    await mock_stream_client.event_bus.start()

    emitted = await mock_stream_client.run_mock_stream(symbols=["SPY", "QQQ"], count=3, interval_seconds=0.01)
    await mock_stream_client.event_bus.wait_until_idle()

    assert len(emitted) == 6  # 2 symbols * 3 count
    assert len(ticks) == 6
    assert set(t.symbol for t in ticks) == {"SPY", "QQQ"}

    await mock_stream_client.event_bus.stop()


@pytest.mark.asyncio
async def test_stream_lifecycle_start_stop(mock_stream_client: AlpacaStreamClient) -> None:
    """Verifies start and stop stream lifecycle management."""
    assert mock_stream_client._is_running is False
    await mock_stream_client.start_stream()
    assert mock_stream_client._is_running is True
    await mock_stream_client.stop_stream()
    assert mock_stream_client._is_running is False


@pytest.mark.asyncio
async def test_invalid_quote_filtered(mock_stream_client: AlpacaStreamClient) -> None:
    """Verifies corrupt quotes (e.g. inverted bid-ask or zero prices) are discarded."""
    ticks: list[MarketTickEvent] = []
    mock_stream_client.event_bus.subscribe(MarketTickEvent, lambda e: ticks.append(e))

    class CorruptQuote:
        symbol = "SPY"
        bid_price = 10.0
        ask_price = 5.0  # Inverted! Ask < Bid

    await mock_stream_client._handle_stream_quote(CorruptQuote())
    assert len(ticks) == 0  # Must not be dispatched
