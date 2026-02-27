# WhatsApp Options Trading Agent

Receives structured trade commands via WhatsApp and executes options orders on AngelOne Smart API.

---

## Setup Guide

### Step 1 — AngelOne Smart API credentials

1. Go to https://smartapi.angelbroking.com and log in
2. Click **My Apps → Create App**
3. Note your **API Key**
4. Enable TOTP: Go to AngelOne app → Profile → Security → Enable TOTP
5. When you scan the QR code, the underlying value is your **TOTP Secret** (32-char base32 string)
   - Use a QR code reader app to extract it, or AngelOne support can provide it directly

### Step 2 — WhatsApp Business API (Meta)

1. Go to https://developers.facebook.com and create a developer account
2. Create a new App → Choose **Business** type
3. Add **WhatsApp** product to your app
4. Under **WhatsApp → API Setup**:
   - Note your **Phone Number ID**
   - Generate a **Temporary Access Token** (valid 24h) or set up a System User for permanent token
5. Under **WhatsApp → Configuration**, set your Webhook URL:
   - URL: `https://your-server.com/webhook`
   - Verify Token: the value you put in `VERIFY_TOKEN` env var
   - Subscribe to: `messages`

> **WHY you need a public HTTPS URL**: Meta's servers push messages to your webhook.
> For local development, use [ngrok](https://ngrok.com): `ngrok http 8000`

### Step 3 — Instrument Token Lookup

AngelOne requires a numeric `symboltoken` for every order.

1. Download the instrument master:
   ```
   curl -o OpenAPIScripMaster.json \
     "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
   ```
2. Implement `_resolve_token()` in `angel_client.py` to look up the token for your trading symbol

### Step 4 — Install & Run

```bash
# Clone / copy this project
cd whatsapp-options-agent

# Install dependencies
pip install -r requirements.txt

# Copy and fill in your credentials
cp .env.example .env
nano .env

# Run the server
uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## Command Reference

| Command | Meaning |
|---|---|
| `BUY NIFTY 24000 CE 2` | Buy 2 lots of NIFTY 24000 Call (current expiry) |
| `SELL BANKNIFTY 52000 PE 1` | Sell 1 lot of BANKNIFTY 52000 Put |
| `BUY FINNIFTY 21500 CE 3` | Buy 3 lots of FINNIFTY 21500 Call |
| `STATUS` | Show current open positions |

Commands are **case-insensitive** and must come from **whitelisted numbers only**.

---

## Risk Controls

Configured via env vars:

| Var | Default | What it does |
|---|---|---|
| `MAX_QTY` | 10 | Max lots per single order |
| `MAX_ORDER_VALUE` | 100000 | Max estimated order value (₹) |

Strikes not divisible by 50 are auto-rejected.

---

## Architecture

```
WhatsApp message (from allowed number)
  → POST /webhook (FastAPI)
    → parse_command()         # regex parser
      → RiskGate.check()      # sanity checks
        → AngelOneClient.place_options_order()   # Smart API
          → send reply back via WhatsApp
```

---

## Production Checklist

- [ ] Replace temporary Meta token with permanent **System User token**
- [ ] Implement `_resolve_token()` with daily instrument master download
- [ ] Add a scheduler (APScheduler) to refresh AngelOne session daily at 8 AM
- [ ] Switch `producttype` from `INTRADAY` to `CARRYFORWARD` if you want overnight positions
- [ ] Add a database (SQLite/Postgres) to log all orders for audit trail
- [ ] Set up alerts for order failures (email/Telegram)
- [ ] Deploy on a VPS with systemd or Docker for reliability
