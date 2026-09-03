"""
src/execution/order_builder.py
OCC symbol formatting and multi-leg defined-risk options order constructors.

Strictly follows:
- 21-character Options Clearing Corporation (OCC) standard format
- Alpaca multi-leg order specifications (LimitOrderRequest with OrderClass.MLEG and OptionLegRequest)
- Defined-risk mathematical verification for Bull Put, Bear Call, Iron Condors, and Debit Spreads.
"""

from datetime import date, datetime
import math
import re
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest

from src.core.config import settings
from src.core.exceptions import OCCFormattingError, RiskGateViolationError
from src.risk.hard_gates import TradeProposal


def format_occ_symbol(
    underlying: str,
    expiration: Union[date, datetime, str],
    option_type: Union[str, Literal["call", "put", "C", "P"]],
    strike: float,
    padded: bool = True,
) -> str:
    """
    Constructs an OCC options symbol adhering to standard symbology.
    Format: [ROOT (up to 6 chars, right-padded with space if padded=True)][YYMMDD][T][STRIKE (8 digits, price * 1000)]

    Example (padded=True, 21-character canonical OCC):
        format_occ_symbol("SPY", date(2026, 9, 18), "call", 560.0, padded=True)
        -> "SPY   260918C00560000" (Length: 21)

    Example (padded=False, raw ticker for Alpaca REST/WebSocket endpoints):
        format_occ_symbol("SPY", date(2026, 9, 18), "call", 560.0, padded=False)
        -> "SPY260918C00560000" (Length: 18)

    Args:
        underlying: Root ticker symbol (1-6 alphanumeric characters, e.g. SPY, QQQ, NVDA).
        expiration: Expiration date as date, datetime, or ISO string "YYYY-MM-DD".
        option_type: 'call', 'put', 'c', or 'p'.
        strike: Strike price (e.g. 560.0 or 125.50).
        padded: If True, right-pads root to 6 chars yielding exactly 21 chars.

    Returns:
        Formatted OCC symbol string.
    """
    clean_root = underlying.strip().upper()
    if not (1 <= len(clean_root) <= 6) or not clean_root.isalnum():
        raise OCCFormattingError(
            clean_root,
            f"Root ticker must be 1 to 6 alphanumeric characters, got '{underlying}'",
        )

    # Parse expiration date to YYMMDD
    if isinstance(expiration, str):
        # Handles "YYYY-MM-DD" or "YYMMDD"
        exp_clean = expiration.strip()
        if len(exp_clean) == 10 and "-" in exp_clean:
            parsed_dt = datetime.strptime(exp_clean, "%Y-%m-%d")
            yymmdd = parsed_dt.strftime("%y%m%d")
        elif len(exp_clean) == 6 and exp_clean.isdigit():
            yymmdd = exp_clean
        else:
            raise OCCFormattingError(str(expiration), "Invalid expiration date string format")
    elif isinstance(expiration, (date, datetime)):
        yymmdd = expiration.strftime("%y%m%d")
    else:
        raise OCCFormattingError(str(expiration), "Unsupported expiration date type")

    # Option type: C or P
    opt_norm = str(option_type).strip().upper()
    if opt_norm in ("CALL", "C"):
        type_char = "C"
    elif opt_norm in ("PUT", "P"):
        type_char = "P"
    else:
        raise OCCFormattingError(str(option_type), f"Invalid option type: '{option_type}' (expected Call or Put)")

    # Strike price: 8 digits = strike * 1000, zero-padded
    if strike <= 0:
        raise OCCFormattingError(str(strike), f"Strike price must be strictly positive, got {strike}")
    strike_int = int(round(strike * 1000.0))
    strike_str = f"{strike_int:08d}"

    # Root formatting: right-padded to 6 characters if padded=True
    formatted_root = clean_root.ljust(6) if padded else clean_root

    result = f"{formatted_root}{yymmdd}{type_char}{strike_str}"
    if padded and len(result) != 21:
        raise OCCFormattingError(result, f"Padded OCC symbol must be exactly 21 characters, got {len(result)}")

    return result


def to_alpaca_symbol(symbol: str) -> str:
    """Strips internal whitespace from a 21-character OCC symbol for Alpaca API submission."""
    clean = symbol.strip().upper()
    # Removes spaces between root and date
    return re.sub(r"\s+", "", clean)


# ------------------------------------------------------------------------------
# Multi-Leg Defined-Risk Spread Constructors
# ------------------------------------------------------------------------------

def build_bull_put_spread(
    underlying: str,
    expiration: Union[date, datetime, str],
    short_strike: float,
    long_strike: float,
    credit: float,
    quantity: int = 1,
    dte: int = 30,
    is_tactical: bool = False,
    spread_slippage_pct: Optional[float] = None,
    spread_slippage_dollars: Optional[float] = None,
) -> Tuple[LimitOrderRequest, TradeProposal]:
    """
    Constructs a defined-risk Bull Put Credit Spread (Sell higher Put, Buy lower Put).
    Collateral required = (short_strike - long_strike) * 100 * quantity.
    Max loss = (short_strike - long_strike - credit) * 100 * quantity.
    """
    if short_strike <= long_strike:
        raise ValueError(
            f"Bull Put Spread requires short_strike ({short_strike}) > long_strike ({long_strike})"
        )
    if credit <= 0.0:
        raise ValueError(f"Net credit must be positive, got ${credit:.2f}")

    strike_width = short_strike - long_strike
    if credit >= strike_width:
        raise ValueError(f"Net credit (${credit:.2f}) cannot equal or exceed strike width (${strike_width:.2f})")

    max_loss_per_contract = round((strike_width - credit) * 100.0, 2)
    required_margin_per_contract = round(strike_width * 100.0, 2)

    short_sym = format_occ_symbol(underlying, expiration, "P", short_strike, padded=False)
    long_sym = format_occ_symbol(underlying, expiration, "P", long_strike, padded=False)

    legs = [
        OptionLegRequest(
            symbol=short_sym,
            ratio_qty=1.0,
            side=OrderSide.SELL,
            position_intent=PositionIntent.SELL_TO_OPEN,
        ),
        OptionLegRequest(
            symbol=long_sym,
            ratio_qty=1.0,
            side=OrderSide.BUY,
            position_intent=PositionIntent.BUY_TO_OPEN,
        ),
    ]

    order_request = LimitOrderRequest(
        order_class=OrderClass.MLEG,
        qty=float(quantity),
        time_in_force=TimeInForce.DAY,
        limit_price=round(credit, 2),
        legs=legs,
    )

    proposal = TradeProposal(
        symbol=underlying.upper(),
        strategy_name="Bull Put Credit Spread",
        legs=[
            {"symbol": short_sym, "side": "sell", "strike": short_strike, "type": "put"},
            {"symbol": long_sym, "side": "buy", "strike": long_strike, "type": "put"},
        ],
        quantity=quantity,
        max_loss_per_contract=max_loss_per_contract,
        target_credit_per_contract=credit * 100.0,
        required_margin_per_contract=required_margin_per_contract,
        dte=dte,
        is_tactical=is_tactical,
        spread_slippage_pct=spread_slippage_pct,
        spread_slippage_dollars=spread_slippage_dollars,
    )

    return order_request, proposal


def build_bear_call_spread(
    underlying: str,
    expiration: Union[date, datetime, str],
    short_strike: float,
    long_strike: float,
    credit: float,
    quantity: int = 1,
    dte: int = 30,
    is_tactical: bool = False,
    spread_slippage_pct: Optional[float] = None,
    spread_slippage_dollars: Optional[float] = None,
) -> Tuple[LimitOrderRequest, TradeProposal]:
    """
    Constructs a defined-risk Bear Call Credit Spread (Sell lower Call, Buy higher Call).
    Collateral required = (long_strike - short_strike) * 100 * quantity.
    Max loss = (long_strike - short_strike - credit) * 100 * quantity.
    """
    if short_strike >= long_strike:
        raise ValueError(
            f"Bear Call Spread requires short_strike ({short_strike}) < long_strike ({long_strike})"
        )
    if credit <= 0.0:
        raise ValueError(f"Net credit must be positive, got ${credit:.2f}")

    strike_width = long_strike - short_strike
    if credit >= strike_width:
        raise ValueError(f"Net credit (${credit:.2f}) cannot equal or exceed strike width (${strike_width:.2f})")

    max_loss_per_contract = round((strike_width - credit) * 100.0, 2)
    required_margin_per_contract = round(strike_width * 100.0, 2)

    short_sym = format_occ_symbol(underlying, expiration, "C", short_strike, padded=False)
    long_sym = format_occ_symbol(underlying, expiration, "C", long_strike, padded=False)

    legs = [
        OptionLegRequest(
            symbol=short_sym,
            ratio_qty=1.0,
            side=OrderSide.SELL,
            position_intent=PositionIntent.SELL_TO_OPEN,
        ),
        OptionLegRequest(
            symbol=long_sym,
            ratio_qty=1.0,
            side=OrderSide.BUY,
            position_intent=PositionIntent.BUY_TO_OPEN,
        ),
    ]

    order_request = LimitOrderRequest(
        order_class=OrderClass.MLEG,
        qty=float(quantity),
        time_in_force=TimeInForce.DAY,
        limit_price=round(credit, 2),
        legs=legs,
    )

    proposal = TradeProposal(
        symbol=underlying.upper(),
        strategy_name="Bear Call Credit Spread",
        legs=[
            {"symbol": short_sym, "side": "sell", "strike": short_strike, "type": "call"},
            {"symbol": long_sym, "side": "buy", "strike": long_strike, "type": "call"},
        ],
        quantity=quantity,
        max_loss_per_contract=max_loss_per_contract,
        target_credit_per_contract=credit * 100.0,
        required_margin_per_contract=required_margin_per_contract,
        dte=dte,
        is_tactical=is_tactical,
        spread_slippage_pct=spread_slippage_pct,
        spread_slippage_dollars=spread_slippage_dollars,
    )

    return order_request, proposal


def build_iron_condor(
    underlying: str,
    expiration: Union[date, datetime, str],
    put_long_strike: float,
    put_short_strike: float,
    call_short_strike: float,
    call_long_strike: float,
    net_credit: float,
    quantity: int = 1,
    dte: int = 30,
    spread_slippage_pct: Optional[float] = None,
    spread_slippage_dollars: Optional[float] = None,
) -> Tuple[LimitOrderRequest, TradeProposal]:
    """
    Constructs a 4-leg Iron Condor:
    Put wing: Buy put_long_strike, Sell put_short_strike.
    Call wing: Sell call_short_strike, Buy call_long_strike.
    Hierarchy: put_long < put_short < call_short < call_long.
    """
    if not (put_long_strike < put_short_strike < call_short_strike < call_long_strike):
        raise ValueError(
            f"Iron Condor strikes must satisfy: {put_long_strike} < {put_short_strike} < "
            f"{call_short_strike} < {call_long_strike}"
        )

    put_width = put_short_strike - put_long_strike
    call_width = call_long_strike - call_short_strike
    max_wing_width = max(put_width, call_width)

    if net_credit <= 0.0 or net_credit >= max_wing_width:
        raise ValueError(f"Net credit (${net_credit:.2f}) must be positive and less than wing width (${max_wing_width:.2f})")

    # Margin is held on the wider wing (stock cannot breach both sides at once)
    max_loss_per_contract = round((max_wing_width - net_credit) * 100.0, 2)
    required_margin_per_contract = round(max_wing_width * 100.0, 2)

    put_long_sym = format_occ_symbol(underlying, expiration, "P", put_long_strike, padded=False)
    put_short_sym = format_occ_symbol(underlying, expiration, "P", put_short_strike, padded=False)
    call_short_sym = format_occ_symbol(underlying, expiration, "C", call_short_strike, padded=False)
    call_long_sym = format_occ_symbol(underlying, expiration, "C", call_long_strike, padded=False)

    legs = [
        OptionLegRequest(
            symbol=put_long_sym,
            ratio_qty=1.0,
            side=OrderSide.BUY,
            position_intent=PositionIntent.BUY_TO_OPEN,
        ),
        OptionLegRequest(
            symbol=put_short_sym,
            ratio_qty=1.0,
            side=OrderSide.SELL,
            position_intent=PositionIntent.SELL_TO_OPEN,
        ),
        OptionLegRequest(
            symbol=call_short_sym,
            ratio_qty=1.0,
            side=OrderSide.SELL,
            position_intent=PositionIntent.SELL_TO_OPEN,
        ),
        OptionLegRequest(
            symbol=call_long_sym,
            ratio_qty=1.0,
            side=OrderSide.BUY,
            position_intent=PositionIntent.BUY_TO_OPEN,
        ),
    ]

    order_request = LimitOrderRequest(
        order_class=OrderClass.MLEG,
        qty=float(quantity),
        time_in_force=TimeInForce.DAY,
        limit_price=round(net_credit, 2),
        legs=legs,
    )

    proposal = TradeProposal(
        symbol=underlying.upper(),
        strategy_name="Iron Condor",
        legs=[
            {"symbol": put_long_sym, "side": "buy", "strike": put_long_strike, "type": "put"},
            {"symbol": put_short_sym, "side": "sell", "strike": put_short_strike, "type": "put"},
            {"symbol": call_short_sym, "side": "sell", "strike": call_short_strike, "type": "call"},
            {"symbol": call_long_sym, "side": "buy", "strike": call_long_strike, "type": "call"},
        ],
        quantity=quantity,
        max_loss_per_contract=max_loss_per_contract,
        target_credit_per_contract=net_credit * 100.0,
        required_margin_per_contract=required_margin_per_contract,
        dte=dte,
        is_tactical=False,
        spread_slippage_pct=spread_slippage_pct,
        spread_slippage_dollars=spread_slippage_dollars,
    )

    return order_request, proposal
