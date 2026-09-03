"""Market data ingestion, Greeks engine, and regime detection package."""

from src.data.alpaca_stream import AlpacaStreamClient
from src.data.greeks_engine import (
    GreeksResult,
    black_scholes_price,
    calculate_greeks,
    calculate_implied_volatility,
)
from src.data.chain_parser import (
    ParsedContract,
    StrikeRow,
    ExpirationLadder,
    ParsedOptionChain,
    parse_occ_symbol,
    OptionChainParser,
)
from src.data.regime_detector import (
    MarketRegime,
    TrendDirection,
    RegimeClassification,
    RegimeDetector,
)

__all__ = [
    "AlpacaStreamClient",
    "GreeksResult",
    "black_scholes_price",
    "calculate_greeks",
    "calculate_implied_volatility",
    "ParsedContract",
    "StrikeRow",
    "ExpirationLadder",
    "ParsedOptionChain",
    "parse_occ_symbol",
    "OptionChainParser",
    "MarketRegime",
    "TrendDirection",
    "RegimeClassification",
    "RegimeDetector",
]
