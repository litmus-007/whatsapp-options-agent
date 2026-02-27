"""
parser.py — Structured command parser
======================================
WHY regex and not AI: For financial orders, deterministic parsing is safer.
A wrong AI interpretation could cost real money.

Supported formats:
  BUY|SELL  SYMBOL  STRIKE  CE|PE  QTY     → place a new order
  SQUAREOFF                                 → close ALL open positions at market
  SQUAREOFF  SYMBOL  STRIKE  CE|PE          → close a specific position at market
  STATUS                                    → view open positions

Examples:
  BUY NIFTY 24000 CE 50
  SELL BANKNIFTY 52000 PE 25
  SQUAREOFF                        ← closes everything
  SQUAREOFF NIFTY 24000 CE         ← closes only this leg
"""

import re
from typing import Optional

# WHY these symbols: these are the F&O-eligible index options on NSE
VALID_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}

COMMAND_PATTERN = re.compile(
    r"^(BUY|SELL)\s+"          # action
    r"([A-Z]+)\s+"             # symbol
    r"(\d{4,6})\s+"            # strike price (4–6 digits)
    r"(CE|PE)\s+"              # option type
    r"(\d+)$"                  # quantity
)

# SQUAREOFF ALL:      "SQUAREOFF"
# SQUAREOFF SPECIFIC: "SQUAREOFF NIFTY 24000 CE"
SQUAREOFF_ALL_PATTERN = re.compile(r"^SQUAREOFF$")
SQUAREOFF_LEG_PATTERN = re.compile(
    r"^SQUAREOFF\s+"
    r"([A-Z]+)\s+"             # symbol
    r"(\d{4,6})\s+"            # strike
    r"(CE|PE)$"                # option type
)


def parse_command(text: str) -> Optional[dict]:
    """
    Parse a structured trade command string.
    Returns a dict with trade details, or None if the command is invalid.

    Return shapes:
      {"action": "BUY"|"SELL", "symbol": ..., "strike": ..., "option_type": ..., "qty": ...}
      {"action": "SQUAREOFF_ALL"}
      {"action": "SQUAREOFF_LEG", "symbol": ..., "strike": ..., "option_type": ...}
    """
    text = text.strip().upper()

    # ── SQUAREOFF ALL ────────────────────────────────────────────────────────
    if SQUAREOFF_ALL_PATTERN.match(text):
        return {"action": "SQUAREOFF_ALL"}

    # ── SQUAREOFF specific leg ────────────────────────────────────────────────
    match = SQUAREOFF_LEG_PATTERN.match(text)
    if match:
        symbol, strike, option_type = match.groups()
        if symbol not in VALID_SYMBOLS:
            return None
        return {
            "action":      "SQUAREOFF_LEG",
            "symbol":      symbol,
            "strike":      int(strike),
            "option_type": option_type,
        }

    # ── BUY / SELL ────────────────────────────────────────────────────────────
    match = COMMAND_PATTERN.match(text)
    if not match:
        return None

    action, symbol, strike, option_type, qty = match.groups()
    if symbol not in VALID_SYMBOLS:
        return None

    return {
        "action":      action,           # "BUY" or "SELL"
        "symbol":      symbol,           # e.g. "NIFTY"
        "strike":      int(strike),      # e.g. 24000
        "option_type": option_type,      # "CE" or "PE"
        "qty":         int(qty),         # e.g. 50
    }
