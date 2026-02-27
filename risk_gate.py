"""
risk_gate.py — Pre-trade risk checks
======================================
WHY a risk gate exists: A mistyped command like "BUY NIFTY 24000 CE 5000"
should be caught before it hits the broker. These are your circuit breakers.
"""

from typing import Tuple

# Approximate premium prices for risk calculation
# In production, fetch live LTP from AngelOne before checking order value
APPROX_PREMIUMS = {
    "NIFTY":       150,   # ₹ per unit — rough estimate
    "BANKNIFTY":   300,
    "FINNIFTY":    100,
    "MIDCPNIFTY":  80,
    "SENSEX":      200,
}

LOT_SIZES = {
    "NIFTY":      50,
    "BANKNIFTY":  15,
    "FINNIFTY":   40,
    "MIDCPNIFTY": 75,
    "SENSEX":     10,
}


class RiskGate:
    def __init__(self, max_qty: int, max_order_value: float):
        """
        max_qty         : max number of LOTS per order
        max_order_value : max estimated order value in ₹
        """
        self.max_qty         = max_qty
        self.max_order_value = max_order_value

    def check(self, trade: dict) -> Tuple[bool, str]:
        """
        Run all risk checks. Returns (True, "") if safe, or (False, reason) if rejected.
        """
        # 1. Quantity check
        if trade["qty"] <= 0:
            return False, "Quantity must be positive"

        if trade["qty"] > self.max_qty:
            return False, f"Quantity {trade['qty']} exceeds max allowed {self.max_qty} lots"

        # 2. Strike sanity check — strike should be a round number (multiple of 50 or 100)
        strike = trade["strike"]
        if strike % 50 != 0:
            return False, f"Strike {strike} doesn't look like a valid options strike (should be multiple of 50)"

        # 3. Estimated order value check
        symbol   = trade["symbol"]
        lot_size = LOT_SIZES.get(symbol, 50)
        premium  = APPROX_PREMIUMS.get(symbol, 200)
        est_value = trade["qty"] * lot_size * premium

        if est_value > self.max_order_value:
            return False, (
                f"Estimated order value ₹{est_value:,.0f} exceeds limit ₹{self.max_order_value:,.0f}. "
                f"Reduce qty or update MAX_ORDER_VALUE env var."
            )

        return True, ""
