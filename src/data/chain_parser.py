"""
src/data/chain_parser.py
Parses raw Alpaca options contract objects and chain responses into structured,
typed strike matrices and expiration ladders.

Enforces:
1. Target DTE universe filtering (14–45 DTE primary, 0–7 DTE tactical).
2. Hard liquidity filters (minimum open interest, valid trading status).
3. Bid-Ask spread slippage guards (max 3.0% spread or $0.15/contract).
4. Automatic OCC symbol decomposition and strike alignment.
"""

from datetime import date, datetime, timezone
import re
from typing import Any, Dict, List, Literal, Optional, Tuple, Union
from pydantic import BaseModel, Field

from src.core.config import settings
from src.core.exceptions import OCCFormattingError
from src.data.greeks_engine import GreeksResult, calculate_greeks, calculate_implied_volatility


# OCC Symbol Regex: e.g. SPY260918C00560000
OCC_REGEX = re.compile(r"^([A-Z]{1,6})\s*(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


class ParsedContract(BaseModel):
    """Strongly-typed individual option contract with quote and liquidity metrics."""

    symbol: str = Field(description="21-character OCC option symbol")
    underlying_symbol: str = Field(description="Root underlying symbol (e.g. SPY)")
    expiration_date: str = Field(description="Expiration date (YYYY-MM-DD)")
    dte: int = Field(ge=0, description="Days to expiration")
    strike_price: float = Field(gt=0.0, description="Option strike price")
    option_type: Literal["call", "put"] = Field(description="'call' or 'put'")
    tradable: bool = Field(default=True, description="Alpaca tradability flag")
    open_interest: int = Field(default=0, ge=0, description="Reported open interest")
    close_price: Optional[float] = Field(default=None, description="Previous day closing price")

    # Real-Time Quote Attributes (when enriched)
    bid: Optional[float] = Field(default=None, ge=0.0, description="Current best bid")
    ask: Optional[float] = Field(default=None, ge=0.0, description="Current best ask")
    mid: Optional[float] = Field(default=None, ge=0.0, description="Midpoint price")
    spread: Optional[float] = Field(default=None, ge=0.0, description="Absolute bid-ask spread")
    slippage_pct: Optional[float] = Field(default=None, ge=0.0, description="Spread as fraction of mid")
    is_liquid: bool = Field(default=True, description="Passes liquidity and slippage guards")

    # Greeks calculation
    greeks: Optional[GreeksResult] = Field(default=None, description="Black-Scholes analytical Greeks")


class StrikeRow(BaseModel):
    """Container for call and put options at the same strike price."""

    strike: float = Field(gt=0.0, description="Strike price")
    call: Optional[ParsedContract] = Field(default=None, description="Call option contract")
    put: Optional[ParsedContract] = Field(default=None, description="Put option contract")


class ExpirationLadder(BaseModel):
    """Complete strike matrix for a single expiration date."""

    expiration_date: str = Field(description="Expiration date (YYYY-MM-DD)")
    dte: int = Field(ge=0, description="Days to expiration")
    strikes: Dict[float, StrikeRow] = Field(default_factory=dict, description="Strike price mapping")

    def get_strike(self, strike: float) -> Optional[StrikeRow]:
        """Returns StrikeRow for a specific strike price."""
        return self.strikes.get(strike)

    def get_sorted_strikes(self) -> List[float]:
        """Returns all strike prices sorted in ascending order."""
        return sorted(list(self.strikes.keys()))

    def get_put_spread(
        self,
        short_strike: float,
        long_strike: float,
    ) -> Optional[Tuple[ParsedContract, ParsedContract]]:
        """
        Retrieves legs for a put credit/debit spread.
        For a Bull Put credit spread, short_strike > long_strike.
        """
        short_row = self.strikes.get(short_strike)
        long_row = self.strikes.get(long_strike)
        if short_row and short_row.put and long_row and long_row.put:
            return short_row.put, long_row.put
        return None

    def get_call_spread(
        self,
        short_strike: float,
        long_strike: float,
    ) -> Optional[Tuple[ParsedContract, ParsedContract]]:
        """
        Retrieves legs for a call credit/debit spread.
        For a Bear Call credit spread, short_strike < long_strike.
        """
        short_row = self.strikes.get(short_strike)
        long_row = self.strikes.get(long_strike)
        if short_row and short_row.call and long_row and long_row.call:
            return short_row.call, long_row.call
        return None


class ParsedOptionChain(BaseModel):
    """Full option chain organized by expiration and strike ladders."""

    underlying_symbol: str = Field(description="Underlying ticker (e.g. SPY)")
    underlying_price: float = Field(gt=0.0, description="Reference underlying price")
    as_of: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Snapshot timestamp",
    )
    expirations: Dict[str, ExpirationLadder] = Field(
        default_factory=dict,
        description="Expiration dates mapped to strike ladders",
    )
    total_contracts_parsed: int = Field(ge=0, description="Total raw contracts processed")
    liquid_contracts_count: int = Field(ge=0, description="Contracts passing liquidity thresholds")

    def get_expiration(self, exp_date: str) -> Optional[ExpirationLadder]:
        """Returns the strike ladder for a specific expiration date."""
        return self.expirations.get(exp_date)

    def get_expirations_in_dte(self, min_dte: int, max_dte: int) -> List[ExpirationLadder]:
        """Returns all expiration ladders within specified DTE boundaries."""
        return [
            ladder for ladder in self.expirations.values()
            if min_dte <= ladder.dte <= max_dte
        ]


def parse_occ_symbol(symbol: str) -> Tuple[str, str, Literal["call", "put"], float]:
    """
    Decomposes an OCC options symbol into its constituent elements.
    Format: [ROOT][YYMMDD][C/P][STRIKE 8-DIGITS (price * 1000)]
    Example: SPY260918C00560000 -> ('SPY', '2026-09-18', 'call', 560.0)
    """
    clean_sym = symbol.strip().upper()
    match = OCC_REGEX.match(clean_sym)
    if not match:
        raise OCCFormattingError(symbol, "Failed OCC regex match (expected e.g. SPY260918C00560000)")

    root, yy, mm, dd, opt_char, strike_raw = match.groups()
    exp_date = f"20{yy}-{mm}-{dd}"
    opt_type: Literal["call", "put"] = "call" if opt_char == "C" else "put"
    strike = int(strike_raw) / 1000.0

    return root, exp_date, opt_type, strike


class OptionChainParser:
    """
    Engine to parse raw Alpaca option contracts into structured, validated strike ladders.
    """

    @staticmethod
    def parse_contracts(
        contracts: List[Any],
        underlying_symbol: str,
        underlying_price: float,
        quotes_map: Optional[Dict[str, Any]] = None,
        min_open_interest: int = 10,
        max_slippage_pct: Optional[float] = None,
        max_slippage_dollars: Optional[float] = None,
        min_dte: Optional[int] = None,
        max_dte: Optional[int] = None,
        risk_free_rate: float = 0.045,
    ) -> ParsedOptionChain:
        """
        Parses a list of Alpaca OptionContract models or dictionaries into a ParsedOptionChain.

        Args:
            contracts: Raw contracts list from Alpaca SDK or API.
            underlying_symbol: Root ticker symbol.
            underlying_price: Current reference price of underlying.
            quotes_map: Optional mapping of OCC symbol -> {bid, ask, iv}.
            min_open_interest: Minimum open interest threshold for liquidity.
            max_slippage_pct: Maximum allowed bid-ask spread as % of mid (default from settings).
            max_slippage_dollars: Maximum allowed bid-ask spread in dollars (default from settings).
            min_dte: Minimum days to expiration to include.
            max_dte: Maximum days to expiration to include.
            risk_free_rate: Annualized rate for Greeks calculation.

        Returns:
            Structured ParsedOptionChain object.
        """
        max_slip_pct = max_slippage_pct or settings.MAX_SLIPPAGE_PCT
        max_slip_dlr = max_slippage_dollars or settings.MAX_SLIPPAGE_DOLLARS
        quotes = quotes_map or {}
        today = date.today()

        expirations_dict: Dict[str, ExpirationLadder] = {}
        total_parsed = 0
        liquid_count = 0

        for raw in contracts:
            total_parsed += 1

            # Extract fields whether dict or object
            symbol = str(getattr(raw, "symbol", "") or raw.get("symbol", ""))
            if not symbol:
                continue

            try:
                root, exp_str, opt_type, strike = parse_occ_symbol(symbol)
            except OCCFormattingError:
                continue

            # Calculate DTE
            try:
                exp_d = date.fromisoformat(exp_str)
                dte = (exp_d - today).days
            except ValueError:
                continue

            # DTE Universe Filtering
            if min_dte is not None and dte < min_dte:
                continue
            if max_dte is not None and dte > max_dte:
                continue

            tradable = bool(getattr(raw, "tradable", True) if hasattr(raw, "tradable") else raw.get("tradable", True))
            oi = int(getattr(raw, "open_interest", 0) or (raw.get("open_interest", 0) or 0))
            close_px = getattr(raw, "close_price", None) or raw.get("close_price", None)
            if close_px is not None:
                close_px = float(close_px)

            # Extract real-time quotes if available in quotes_map
            quote_info = quotes.get(symbol, {})
            bid = quote_info.get("bid")
            ask = quote_info.get("ask")

            mid = None
            spread = None
            slip_pct = None
            is_liquid = tradable and (oi >= min_open_interest)

            if bid is not None and ask is not None and bid > 0 and ask >= bid:
                mid = (bid + ask) / 2.0
                spread = ask - bid
                slip_pct = (spread / mid) if mid > 0 else 0.0

                # Check strict slippage gate
                if spread > max_slip_dlr or slip_pct > max_slip_pct:
                    is_liquid = False

            # Calculate Greeks if quote exists and contract is liquid
            greeks = None
            if mid is not None and mid > 0 and dte > 0:
                time_to_exp = dte / 365.0
                iv = quote_info.get("implied_volatility")
                if not iv or iv <= 0:
                    iv = calculate_implied_volatility(
                        market_price=mid,
                        spot=underlying_price,
                        strike=strike,
                        time_to_exp=time_to_exp,
                        rate=risk_free_rate,
                        option_type=opt_type,
                    )
                greeks = calculate_greeks(
                    spot=underlying_price,
                    strike=strike,
                    time_to_exp=time_to_exp,
                    rate=risk_free_rate,
                    vol=float(iv),
                    option_type=opt_type,
                )

            contract = ParsedContract(
                symbol=symbol,
                underlying_symbol=root,
                expiration_date=exp_str,
                dte=dte,
                strike_price=strike,
                option_type=opt_type,
                tradable=tradable,
                open_interest=oi,
                close_price=close_px,
                bid=bid,
                ask=ask,
                mid=mid,
                spread=spread,
                slippage_pct=slip_pct,
                is_liquid=is_liquid,
                greeks=greeks,
            )

            if is_liquid:
                liquid_count += 1

            # Insert into ExpirationLadder and StrikeRow
            if exp_str not in expirations_dict:
                expirations_dict[exp_str] = ExpirationLadder(
                    expiration_date=exp_str,
                    dte=dte,
                )

            ladder = expirations_dict[exp_str]
            if strike not in ladder.strikes:
                ladder.strikes[strike] = StrikeRow(strike=strike)

            row = ladder.strikes[strike]
            if opt_type == "call":
                row.call = contract
            else:
                row.put = contract

        return ParsedOptionChain(
            underlying_symbol=underlying_symbol,
            underlying_price=underlying_price,
            expirations=expirations_dict,
            total_contracts_parsed=total_parsed,
            liquid_contracts_count=liquid_count,
        )
