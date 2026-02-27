"""
WhatsApp Options Trading Agent
================================
Listens to WhatsApp Business API webhooks, parses structured trade commands,
and executes options orders on AngelOne Smart API.

Command format:
  BUY NIFTY 24000 CE 50       → Buy 50 qty of NIFTY 24000 CE (nearest expiry)
  SELL BANKNIFTY 52000 PE 25  → Sell 25 qty of BANKNIFTY 52000 PE
  STATUS                      → Get current open positions
"""

import os
import logging
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse
import httpx

from parser import parse_command
from angel_client import AngelOneClient
from risk_gate import RiskGate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="WhatsApp Options Agent")

# ── Env vars (set these in .env or your deployment environment) ──────────────
VERIFY_TOKEN      = os.environ["VERIFY_TOKEN"]           # any secret string you choose
WHATSAPP_TOKEN    = os.environ["WHATSAPP_TOKEN"]         # from Meta Developer Console
PHONE_NUMBER_ID   = os.environ["PHONE_NUMBER_ID"]        # from Meta Developer Console
ALLOWED_NUMBERS   = set(os.environ["ALLOWED_NUMBERS"].split(","))  # e.g. "919876543210,919123456789"

# ── Clients (initialised once at startup) ────────────────────────────────────
angel = AngelOneClient(
    api_key=os.environ["ANGEL_API_KEY"],
    client_id=os.environ["ANGEL_CLIENT_ID"],
    password=os.environ["ANGEL_PASSWORD"],
    totp_secret=os.environ["ANGEL_TOTP_SECRET"],
)
risk = RiskGate(
    max_qty=int(os.environ.get("MAX_QTY", "100")),
    max_order_value=float(os.environ.get("MAX_ORDER_VALUE", "100000")),  # ₹1 lakh
)


# ── Webhook verification (Meta calls this once when you register the webhook) ─
@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_challenge: str = Query(alias="hub.challenge"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
):
    """
    WHY: Meta sends a GET request with hub.verify_token to confirm
    your server owns this URL before it starts pushing messages.
    """
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


# ── Main message handler ──────────────────────────────────────────────────────
@app.post("/webhook")
async def receive_message(request: Request):
    """
    WHY: Meta pushes every incoming WhatsApp message to this endpoint.
    We parse the command, validate it, execute the trade, and reply.
    """
    body = await request.json()
    logger.info(f"Incoming webhook: {body}")

    try:
        # Navigate Meta's nested webhook payload
        entry = body["entry"][0]["changes"][0]["value"]
        messages = entry.get("messages")
        if not messages:
            return {"status": "no_messages"}  # delivery receipts etc — ignore

        msg = messages[0]
        from_number = msg["from"]          # e.g. "919876543210"
        msg_text    = msg["text"]["body"].strip().upper()
        msg_id      = msg["id"]

    except (KeyError, IndexError):
        logger.warning("Unrecognised webhook shape, ignoring")
        return {"status": "ignored"}

    # ── Security: only accept commands from whitelisted numbers ──────────────
    # WHY: Anyone who finds your webhook URL shouldn't be able to trade
    if from_number not in ALLOWED_NUMBERS:
        logger.warning(f"Rejected message from unauthorised number: {from_number}")
        await send_whatsapp_reply(from_number, "❌ Unauthorised number.")
        return {"status": "unauthorised"}

    # ── STATUS command ────────────────────────────────────────────────────────
    if msg_text == "STATUS":
        positions = angel.get_positions()
        await send_whatsapp_reply(from_number, format_positions(positions))
        return {"status": "ok"}

    # ── Parse trade command ───────────────────────────────────────────────────
    trade = parse_command(msg_text)
    if not trade:
        await send_whatsapp_reply(
            from_number,
            "⚠️ Unrecognised command.\n\n"
            "*Place order:*\nBUY NIFTY 24000 CE 50\nSELL BANKNIFTY 52000 PE 25\n\n"
            "*Square off:*\nSQUAREOFF                   ← close all positions\n"
            "SQUAREOFF NIFTY 24000 CE    ← close specific leg\n\n"
            "*Status:*\nSTATUS"
        )
        return {"status": "parse_error"}

    # ── SQUAREOFF ALL ─────────────────────────────────────────────────────────
    # WHY no risk gate here: squareoff is always a risk-reducing action —
    # it closes existing positions, never opens new ones.
    if trade["action"] == "SQUAREOFF_ALL":
        try:
            results = angel.squareoff_all()
            if not results:
                reply = "📊 No open positions to square off."
            else:
                lines = ["🔴 *Squareoff All — Results:*"]
                for r in results:
                    icon = "✅" if r["status"] == "OK" else "❌"
                    lines.append(f"{icon} {r['tradingsymbol']} {r['side']} {r['qty']} → {r['status']}")
                reply = "\n".join(lines)
        except Exception as e:
            logger.error(f"Squareoff all failed: {e}")
            reply = f"❌ Squareoff failed: {str(e)}"

        await send_whatsapp_reply(from_number, reply)
        return {"status": "ok"}

    # ── SQUAREOFF specific leg ────────────────────────────────────────────────
    if trade["action"] == "SQUAREOFF_LEG":
        try:
            result = angel.squareoff_leg(trade["symbol"], trade["strike"], trade["option_type"])
            reply = (
                f"✅ Position closed!\n"
                f"Order ID: {result['orderid']}\n"
                f"{result['side']} {result['qty']} × {result['tradingsymbol']} @ MARKET"
            )
        except ValueError as e:
            reply = f"⚠️ {str(e)}"
        except Exception as e:
            logger.error(f"Squareoff leg failed: {e}")
            reply = f"❌ Squareoff failed: {str(e)}"

        await send_whatsapp_reply(from_number, reply)
        return {"status": "ok"}

    # ── Risk gate (BUY / SELL only) ───────────────────────────────────────────
    # WHY: Sanity check qty and estimated order value before hitting the broker
    ok, reason = risk.check(trade)
    if not ok:
        await send_whatsapp_reply(from_number, f"🚫 Risk check failed: {reason}")
        return {"status": "risk_rejected"}

    # ── Execute trade ─────────────────────────────────────────────────────────
    try:
        result = angel.place_options_order(trade)
        reply = (
            f"✅ Order placed!\n"
            f"Order ID: {result['orderid']}\n"
            f"{trade['action']} {trade['symbol']} {trade['strike']} {trade['option_type']} × {trade['qty']}\n"
            f"Status: {result.get('status', 'SUBMITTED')}"
        )
    except Exception as e:
        logger.error(f"Order failed: {e}")
        reply = f"❌ Order failed: {str(e)}"

    await send_whatsapp_reply(from_number, reply)
    return {"status": "ok"}


# ── Helper: send a WhatsApp reply ─────────────────────────────────────────────
async def send_whatsapp_reply(to: str, text: str):
    """
    WHY: We use httpx async so the reply doesn't block the webhook response.
    Meta expects your webhook to respond quickly (< 5s) or it retries.
    """
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            logger.error(f"Failed to send reply: {resp.text}")


def format_positions(positions: list) -> str:
    if not positions:
        return "📊 No open positions."
    lines = ["📊 *Open Positions*"]
    for p in positions:
        lines.append(f"• {p['tradingsymbol']}: {p['netqty']} qty @ avg ₹{p['averageprice']}")
    return "\n".join(lines)
