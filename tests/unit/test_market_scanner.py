"""
tests/unit/test_market_scanner.py
Unit tests for MarketScanner service:
1. Quantitative Combined Edge Score calculation: IVR * (ADX / 25.0)
2. Dynamic Whitelist scanning and ranking across 10 assets
3. Optionable price filtering (price >= $10.00)
4. Volatile market movers screener ingestion (mocked and fallback)
5. Regime classification and strategy assignment
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.data.market_scanner import MarketScanner, ScannedCandidate


@pytest.fixture
def scanner() -> MarketScanner:
    """Provides a MarketScanner instance in mock mode."""
    return MarketScanner(mock_mode=True)


def test_compute_edge_score_formula(scanner: MarketScanner) -> None:
    """Verifies Edge Score = IVR * (ADX / 25.0)."""
    # At benchmark ADX = 25.0, factor is 1.0 -> Edge Score == IVR
    score_bench = scanner.compute_edge_score(ivr=60.0, adx=25.0)
    assert score_bench == 60.0

    # Strong momentum: IVR 76.2, ADX 34.8 -> 76.2 * (34.8 / 25.0) = 106.07
    score_high = scanner.compute_edge_score(ivr=76.2, adx=34.8)
    assert score_high == 106.07

    # Weak chop: IVR 34.0, ADX 14.2 -> 34.0 * (14.2 / 25.0) = 19.31
    score_low = scanner.compute_edge_score(ivr=34.0, adx=14.2)
    assert score_low == 19.31


def test_classify_candidate_regime(scanner: MarketScanner) -> None:
    """Verifies correct regime and strategy assignment based on IVR and ADX."""
    # High IV Trending -> Bull Put Credit Spread
    regime, strat = scanner.classify_candidate_regime(ivr=65.0, adx=30.0)
    assert regime == "HIGH_IV_TRENDING"
    assert strat == "Bull Put Credit Spread"

    # High IV Rangebound -> Iron Condor
    regime, strat = scanner.classify_candidate_regime(ivr=65.0, adx=20.0)
    assert regime == "HIGH_IV_RANGEBOUND"
    assert strat == "Iron Condor"

    # Low IV Trending -> Debit Spread
    regime, strat = scanner.classify_candidate_regime(ivr=40.0, adx=28.0)
    assert regime == "LOW_IV_TRENDING"
    assert strat == "Directional Vertical Debit Spread"

    # Low IV Chop -> Cash Preservation
    regime, strat = scanner.classify_candidate_regime(ivr=35.0, adx=15.0)
    assert regime == "LOW_IV_CHOP"
    assert strat == "Preserve Cash (No Trade)"


def test_scan_whitelist_candidates_ranking(scanner: MarketScanner) -> None:
    """Verifies that all 10 whitelist candidates are evaluated and sorted descending."""
    candidates = scanner.scan_whitelist_candidates()
    assert len(candidates) == 10

    # Check strict descending sort order by edge_score
    scores = [c.edge_score for c in candidates]
    assert scores == sorted(scores, reverse=True)

    # Highest edge score in benchmark is NVDA
    top_candidate = candidates[0]
    assert top_candidate.symbol == "NVDA"
    assert top_candidate.edge_score > 100.0
    assert top_candidate.is_volatile_mover is False


@pytest.mark.asyncio
async def test_scan_volatile_market_movers_fallback(scanner: MarketScanner) -> None:
    """Verifies fallback volatile mover ranking and price filtering."""
    movers = await scanner.scan_volatile_market_movers(top_n=5, min_price=10.0)
    assert len(movers) == 5

    # All prices must be >= $10.00
    for m in movers:
        assert m.price >= 10.0
        assert m.is_volatile_mover is True
        assert m.edge_score > 0.0

    # Strict descending sort order
    scores = [m.edge_score for m in movers]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_scan_volatile_market_movers_price_filter() -> None:
    """Verifies that sub-$10 stocks/warrants are filtered out."""
    scanner = MarketScanner(api_key="test_key", secret_key="test_sec", mock_mode=False)

    mock_movers_resp = MagicMock()
    mock_movers_resp.status_code = 200
    mock_movers_resp.json.return_value = {
        "gainers": [
            {"symbol": "ASTLW", "price": 0.0109, "percent_change": 81.67},  # Below $10 and warrant -> exclude
            {"symbol": "PLTR", "price": 32.40, "percent_change": 6.1},     # Valid
        ],
        "losers": [
            {"symbol": "MRNOW", "price": 0.009, "percent_change": -76.4},   # Below $10 -> exclude
            {"symbol": "COIN", "price": 195.80, "percent_change": -7.4},   # Valid
        ],
    }

    mock_actives_resp = MagicMock()
    mock_actives_resp.status_code = 200
    mock_actives_resp.json.return_value = {"most_actives": []}

    with patch("httpx.AsyncClient.get", side_effect=[mock_movers_resp, mock_actives_resp]):
        candidates = await scanner.scan_volatile_market_movers(top_n=10, min_price=10.0)
        symbols = [c.symbol for c in candidates]
        assert "ASTLW" not in symbols
        assert "MRNOW" not in symbols
        assert "PLTR" in symbols
        assert "COIN" in symbols
