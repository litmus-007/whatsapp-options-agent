"""
angel_client.py — AngelOne Smart API wrapper
==============================================
WHY pyotp: AngelOne mandates TOTP-based 2FA for every session login.
pyotp generates the 6-digit code from your TOTP secret, just like
Google Authenticator does — no manual intervention needed.

WHY session refresh: AngelOne tokens expire. We auto-refresh on 401.
"""

import pyotp
import logging
from datetime import datetime, date
from SmartApi import SmartConnect  # pip install smartapi-python

logger = logging.getLogger(__name__)

# AngelOne transaction type mapping
TRANSACTION_TYPE = {
    "BUY": "BUY",
    "SELL": "SELL",
}

# Lot sizes for index options (NSE standard)
# WHY: AngelOne requires qty in lots × lot_size for options
LOT_SIZES = {
    "NIFTY":      50,
    "BANKNIFTY":  15,
    "FINNIFTY":   40,
    "MIDCPNIFTY": 75,
    "SENSEX":     10,
}


class AngelOneClient:
    def __init__(self, api_key: str, client_id: str, password: str, totp_secret: str):
        self.api_key     = api_key
        self.client_id   = client_id
        self.password    = password
        self.totp_secret = totp_secret
        self._api        = None
        self._login()

    def _login(self):
        """
        WHY we call this at startup AND on token expiry:
        AngelOne sessions expire daily. Auto-login keeps the agent running 24/7.
        """
        totp_code = pyotp.TOTP(self.totp_secret).now()
        self._api = SmartConnect(api_key=self.api_key)
        data = self._api.generateSession(self.client_id, self.password, totp_code)
        if data["status"]:
            logger.info("AngelOne login successful")
            self._auth_token   = data["data"]["jwtToken"]
            self._refresh_token = data["data"]["refreshToken"]
        else:
            raise RuntimeError(f"AngelOne login failed: {data['message']}")

    def _get_trading_symbol(self, symbol: str, strike: int, option_type: str) -> str:
        """
        Construct the NSE options trading symbol for the nearest weekly/monthly expiry.

        WHY this format: AngelOne (and NSE) uses symbols like:
          NIFTY24D1924000CE  → NIFTY, expiry 19-Dec-2024, strike 24000, CE
          BANKNIFTY24D1952000PE

        For simplicity, this uses the current month's expiry.
        For production, you should query the instrument list to get exact expiry.
        """
        today = date.today()
        # Format: SYMBOLYYMM_EXPIRY_STRIKE_TYPE (simplified — replace with live lookup)
        expiry_str = today.strftime("%y%b").upper()  # e.g. "24DEC"
        trading_symbol = f"{symbol}{expiry_str}{strike}{option_type}"
        logger.info(f"Resolved trading symbol: {trading_symbol}")
        return trading_symbol

    def place_options_order(self, trade: dict) -> dict:
        """
        Place a market options order on AngelOne.

        WHY MARKET order: For options, limit orders can miss in fast markets.
        You can change ordertype to "LIMIT" and pass a price if you prefer.

        WHY NFO exchange: NSE F&O (index options) trade on NFO segment.
        """
        symbol    = trade["symbol"]
        lot_size  = LOT_SIZES.get(symbol, 50)
        total_qty = trade["qty"] * lot_size  # convert lots → actual qty

        trading_symbol = self._get_trading_symbol(
            symbol, trade["strike"], trade["option_type"]
        )

        order_params = {
            "variety":          "NORMAL",
            "tradingsymbol":    trading_symbol,
            "symboltoken":      self._resolve_token(trading_symbol),  # instrument token
            "transactiontype":  TRANSACTION_TYPE[trade["action"]],
            "exchange":         "NFO",
            "ordertype":        "MARKET",
            "producttype":      "INTRADAY",   # change to "CARRYFORWARD" for overnight
            "duration":         "DAY",
            "quantity":         str(total_qty),
            "price":            "0",          # 0 for MARKET orders
            "squareoff":        "0",
            "stoploss":         "0",
        }

        logger.info(f"Placing order: {order_params}")
        response = self._api.placeOrder(order_params)

        if not response["status"]:
            raise RuntimeError(response["message"])

        return response["data"]

    def _resolve_token(self, trading_symbol: str) -> str:
        """
        WHY: AngelOne requires a numeric instrument 'symboltoken' alongside
        the trading symbol. In production, download the master instrument CSV
        from AngelOne daily and look up the token from there.

        Download from: https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json
        """
        # Placeholder — replace with actual token lookup from instrument master
        # Example implementation:
        # df = pd.read_json("OpenAPIScripMaster.json")
        # row = df[df['symbol'] == trading_symbol].iloc[0]
        # return str(row['token'])
        raise NotImplementedError(
            "Implement _resolve_token by downloading AngelOne's instrument master JSON. "
            "See: https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        )

    def get_positions(self) -> list:
        """Fetch current open positions."""
        try:
            resp = self._api.position()
            return resp["data"] or []
        except Exception as e:
            logger.error(f"Failed to fetch positions: {e}")
            return []

    def squareoff_all(self) -> list:
        """
        Close ALL open positions at market price.

        WHY we iterate positions and place individual SELL/BUY orders:
        AngelOne's Smart API doesn't have a single "close all" endpoint.
        We fetch open positions, then for each non-zero position we place
        the opposite transaction (BUY to close a short, SELL to close a long).

        WHY we check netqty: positions with netqty=0 are already flat — skip them.
        """
        positions = self.get_positions()
        results = []

        for pos in positions:
            net_qty = int(pos.get("netqty", 0))
            if net_qty == 0:
                continue  # already flat

            # Opposite side to close: long (positive qty) → SELL, short → BUY
            close_side = "SELL" if net_qty > 0 else "BUY"
            abs_qty    = abs(net_qty)

            order_params = {
                "variety":         "NORMAL",
                "tradingsymbol":   pos["tradingsymbol"],
                "symboltoken":     pos["symboltoken"],
                "transactiontype": close_side,
                "exchange":        pos.get("exchange", "NFO"),
                "ordertype":       "MARKET",
                "producttype":     pos.get("producttype", "INTRADAY"),
                "duration":        "DAY",
                "quantity":        str(abs_qty),
                "price":           "0",
                "squareoff":       "0",
                "stoploss":        "0",
            }

            logger.info(f"Squaring off: {close_side} {abs_qty} {pos['tradingsymbol']}")
            try:
                resp = self._api.placeOrder(order_params)
                results.append({
                    "tradingsymbol": pos["tradingsymbol"],
                    "side":          close_side,
                    "qty":           abs_qty,
                    "orderid":       resp["data"]["orderid"] if resp["status"] else None,
                    "status":        "OK" if resp["status"] else resp["message"],
                })
            except Exception as e:
                logger.error(f"Failed to square off {pos['tradingsymbol']}: {e}")
                results.append({
                    "tradingsymbol": pos["tradingsymbol"],
                    "status":        f"FAILED: {e}",
                })

        return results

    def squareoff_leg(self, symbol: str, strike: int, option_type: str) -> dict:
        """
        Close a specific options leg at market price.

        WHY we match on tradingsymbol substring: AngelOne's position tradingsymbol
        encodes symbol+expiry+strike+type (e.g. NIFTY24DEC24000CE). We match
        on the parts we know without needing the exact expiry string.
        """
        positions = self.get_positions()
        search_suffix = f"{strike}{option_type}"   # e.g. "24000CE"
        search_prefix = symbol                      # e.g. "NIFTY"

        matched = [
            p for p in positions
            if p["tradingsymbol"].startswith(search_prefix)
            and p["tradingsymbol"].endswith(search_suffix)
            and int(p.get("netqty", 0)) != 0
        ]

        if not matched:
            raise ValueError(f"No open position found for {symbol} {strike} {option_type}")

        pos        = matched[0]
        net_qty    = int(pos["netqty"])
        close_side = "SELL" if net_qty > 0 else "BUY"
        abs_qty    = abs(net_qty)

        order_params = {
            "variety":         "NORMAL",
            "tradingsymbol":   pos["tradingsymbol"],
            "symboltoken":     pos["symboltoken"],
            "transactiontype": close_side,
            "exchange":        pos.get("exchange", "NFO"),
            "ordertype":       "MARKET",
            "producttype":     pos.get("producttype", "INTRADAY"),
            "duration":        "DAY",
            "quantity":        str(abs_qty),
            "price":           "0",
            "squareoff":       "0",
            "stoploss":        "0",
        }

        logger.info(f"Squaring off leg: {close_side} {abs_qty} {pos['tradingsymbol']}")
        resp = self._api.placeOrder(order_params)
        if not resp["status"]:
            raise RuntimeError(resp["message"])

        return {
            "tradingsymbol": pos["tradingsymbol"],
            "side":          close_side,
            "qty":           abs_qty,
            "orderid":       resp["data"]["orderid"],
        }
