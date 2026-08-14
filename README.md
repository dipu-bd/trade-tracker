# Trade Tracker

[![Tests](https://github.com/dipu-bd/trade-tracker/actions/workflows/tests.yml/badge.svg)](https://github.com/dipu-bd/trade-tracker/actions/workflows/tests.yml)
[![Gold Price Slack Alert](https://github.com/dipu-bd/trade-tracker/actions/workflows/gold_price.yml/badge.svg)](https://github.com/dipu-bd/trade-tracker/actions/workflows/gold_price.yml)
[![Deployment CI](https://github.com/dipu-bd/trade-tracker/actions/workflows/server-deploy.yml/badge.svg)](https://github.com/dipu-bd/trade-tracker/actions/workflows/server-deploy.yml)

An active **paper** portfolio builder for crypto, U.S. stocks and ETFs, plus the
original gold-price tracking utilities.

The engine scans the market a few times a day, scores every candidate, sizes
positions against a fixed risk budget, manages stops, rotates stale holdings
out, and emails you the moment the book changes. Nothing is ever sent to a
broker — fills are simulated at scan-time prices with modelled slippage and
commission, and the schema is left broker-ready so live execution would be an
added service rather than a rewrite.

## Links

- [Gold chart](https://dipu-bd.github.io/trade-tracker/)
- [MarketBot Docs](https://marketbot.bitanon.dev/docs)

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.sample .env          # fill in what you need; nothing is mandatory to start

# Create a portfolio — this is where capital and risk appetite are decided
python jobs/portfolio_scan.py --create "Swing Book" --capital 10000 \
    --risk-pct 1.0 --crypto-max-pct 30

# Plan a scan without trading anything
python jobs/portfolio_scan.py --portfolio 1 --sleeve crypto --dry-run

# Run for real
python jobs/portfolio_scan.py --portfolio 1 --sleeve all
```

The crypto sleeve needs **no API key at all** — Crypto.com's public endpoints
are unauthenticated — so a dry run works immediately after checkout.

Run the API with `python -m marketbot` and open `/docs`.

---

## How the strategy works

Swing horizon on daily bars: positions are held days to weeks, and the engine
makes decisions a few times a day rather than tick by tick.

### Market regime gates all new risk

Computed each run from SPY's own moving averages:

| Regime | Condition | Effect |
|---|---|---|
| Bullish | `close > EMA50 > EMA200` | Full risk budget |
| Neutral | `close > EMA200`, below EMA50 | Half budget, only top-decile scores |
| Bearish | `close < EMA200` | No new stock/ETF entries; stops tightened to 1×ATR; crypto budget halved |

### Scoring (0–100)

Blended per asset class from cached daily bars plus a live quote:

- **Stocks** — 20d/60d momentum, EMA stack and ADX, relative volume, gap
  (rewarded 2–15%, penalised past 25% as a chase), RSI sweet spot 45–70, and
  ATR% wide enough to pay but not unhinged.
- **ETFs** — trend only: EMA stack, ADX, 60d/120d momentum. No gap or RVOL
  emphasis, a lower entry bar, `3×ATR` stops, longer holds.
- **Crypto** — momentum, trend and volume with no gap logic (a 24/7 market
  does not gap the way an equity does), `2.5×ATR` stops, higher ATR% floor.

Hard guards subtract and flag the reason: `LOW-PRICE`, `THIN`, `NO-RANGE`,
`WILD`, `OVERBOUGHT`, `NO-TREND`, `EXTENDED`, `52W-HIGH`.

### Entry

Enter when the score clears the threshold, the regime permits, the name is not
already held, and the position-count, per-sector (max 2) and sleeve caps all
allow it. Size is risk-based:

```
qty = (equity × risk_pct × regime_multiplier) ÷ (entry − stop)
```

then clamped by the concentration cap, available cash, and the sleeve budget.

### Exit — first matching rule wins

1. **Hard stop** — price ≤ stop.
2. **Take profit** — at the configured R multiple (default 3R).
3. **Time stop** — held past `max_hold_days` with under +0.5R. Dead money gets
   recycled.
4. **Regime flip** — risk-off cuts equity positions not already past +1R.
5. **Signal decay** — score falls below the exit threshold, or the trend breaks
   (close under EMA20 for stocks, EMA50 for ETFs).
6. **Daily loss breaker** — day P&L ≤ −`daily_loss_pct`: liquidate everything
   and halt new entries for the rest of the day.

Stops only ever ratchet upward: breakeven at +1R, then a chandelier trail at
`high_water − atr_mult × ATR` past +2R.

### Rotation

When the book is full, a candidate scoring ≥ `rotation_edge` points above the
weakest holding's *current* score replaces it — unless that holding is already
trailing in profit. This is what keeps the portfolio active instead of drifting
into an accidental buy-and-hold.

---

## The LLM advisor (optional)

The deterministic rules always run first and produce the plan. If an API key is
configured, an LLM reviews that plan **once per scan** — not once per candidate
— and may narrow it.

| `LLM_ADVISOR_MODE` | Behaviour |
|---|---|
| `off` *(default)* | Never called. Fully deterministic. |
| `annotate` | Advice is stored and shown in the email, but changes nothing. |
| `veto` | May **reject** or **halve** a proposed entry, and may **force an exit** on a holding the rules have not flagged. |

Hard bounds, enforced in code rather than merely requested in the prompt:

- It **cannot invent an entry** the engine did not propose.
- It **cannot raise a size**, widen a stop, or cancel a protective exit.
- It **cannot override** the daily-loss breaker.
- Output is schema-validated and clamped; every verdict is written to
  `llm_advice` with latency and token counts.
- Any failure — timeout, refusal, malformed JSON, raised exception — falls
  through to the deterministic plan and logs why. The LLM is never a hard
  dependency.

Both providers are supported through one interface:

```bash
# Claude, via the official Anthropic SDK
LLM_ADVISOR_MODE=veto
LLM_PROVIDER=anthropic
LLM_MODEL=claude-opus-5
ANTHROPIC_API_KEY=sk-ant-...

# ...or anything speaking the OpenAI protocol (OpenAI, OpenRouter, Groq,
# vLLM, Ollama)
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
LLM_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=...
```

---

## Market data

| Sleeve | Source | Key required |
|---|---|---|
| U.S. stocks & ETFs | Financial Modeling Prep | `FMP_API_KEY` |
| Crypto | Crypto.com public REST | none |

The FMP integration is written for the **free tier**: quotes are batched (one
call covers ~40 symbols), daily bars are cached in `price_bars` and only
refetched when stale, and premium endpoints are tried once then remembered as
unavailable. A daily request budget (`FMP_DAILY_REQUEST_BUDGET`, default 240) is
tracked in the database and spent by priority — open positions first, then
top-ranked candidates. When the budget runs out the scan continues on cached
bars rather than failing.

---

## Email

SMTP via `smtplib`, modelled on the mail service in *lightnovel-crawler*: one
cached connection, NOOP-probed and transparently reconnected, sends serialised
behind a lock.

Defaults target a local **ProtonMail Bridge**, which listens on `127.0.0.1:1025`
with a self-signed certificate — hence `SMTP_TLS_VERIFY=false`:

```bash
SMTP_ENABLED=true
SMTP_SERVER=127.0.0.1     # host.docker.internal when running in Docker
SMTP_PORT=1025
SMTP_USERNAME=you@proton.me
SMTP_PASSWORD=<bridge password>
SMTP_STARTTLS=true
SMTP_TLS_VERIFY=false
NOTIFY_EMAIL=you@proton.me
```

`NOTIFY_MODE=per_run` (default) sends one mail the instant a scan finishes,
listing every add and remove it made; `per_event` sends one mail per change.
A separate daily digest reports equity, open positions and return against the
original investment.

> Running in Docker, the container cannot reach the host's loopback. The deploy
> workflow passes `--add-host=host.docker.internal:host-gateway`; point
> `SMTP_SERVER` at `host.docker.internal`.

---

## Schedule

Runs in-process via APScheduler when `SCHEDULER_ENABLED=true`. All cron
expressions are UTC and configurable:

| Job | Default | Meaning |
|---|---|---|
| `SCAN_CRON_PREOPEN` | `15 13 * * 1-5` | ~09:15 ET, before the open |
| `SCAN_CRON_MAIN` | `45 19 * * 1-5` | ~15:45 ET, the main decision run |
| `SCAN_CRON_CRYPTO` | `0 */4 * * *` | Every four hours, 24/7 |
| `DIGEST_CRON` | `30 21 * * *` | Post-close digest |

---

## API

All routes require the `x-access-token` header (`SERVER_API_TOKEN`).

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/portfolio` | Create a portfolio — capital, risk profile, sleeve caps |
| `GET` | `/api/portfolio` | List portfolios |
| `GET` | `/api/portfolio/{id}` | Detail with live valuation and open positions |
| `PATCH` | `/api/portfolio/{id}` | Update risk settings, or pause it |
| `GET` | `/api/portfolio/{id}/positions` | Positions, open by default |
| `GET` | `/api/portfolio/{id}/trades` | Fill history |
| `GET` | `/api/portfolio/{id}/events` | Add/remove event log |
| `GET` | `/api/portfolio/{id}/history` | Daily equity curve |
| `POST` | `/api/portfolio/{id}/scan` | Run a scan now (`?sleeve=`, `?dry_run=`) |
| `POST` | `/api/portfolio/{id}/digest` | Send the digest email now |
| `POST` | `/api/portfolio/{id}/liquidate` | Close everything — manual kill switch |
| `GET` | `/api/market/candidates` | Ranked candidates without trading |
| `GET` | `/api/market/regime` | Current market regime |

Creating a portfolio is where capital and risk appetite are chosen:

```bash
curl -X POST http://localhost:8000/api/portfolio \
  -H 'x-access-token: <SERVER_API_TOKEN>' \
  -H 'content-type: application/json' \
  -d '{
        "name": "Swing Book",
        "initial_capital": 10000,
        "risk_pct_per_trade": 1.0,
        "max_positions": 8,
        "max_position_pct": 25,
        "daily_loss_pct": 6,
        "crypto_max_pct": 30,
        "notify_email": "you@proton.me"
      }'
```

---

## Database

SQLite by default (`MARKETBOT_DB_URL`), created on first boot — no migration
step. Tables: `portfolios`, `instruments`, `price_bars`, `positions`, `trades`,
`signals`, `scan_runs`, `llm_advice`, `events`, `portfolio_snapshots`,
`api_usage`.

`signals` keeps every scored candidate from every run, which is what makes it
possible to tune the scoring weights against realised results later rather than
taking them on faith.

---

## Tests

```bash
python -m pytest tests/ -q
```

No network access required: a fake provider serves deterministic synthetic bars
through the full engine. Coverage includes indicator correctness against
hand-computed values, risk-based sizing, every exit rule, rotation, paper-fill
slippage and fee math, sleeve and sector caps, the advisor's hard bounds, and
email template rendering.

---

## Disclaimer

This is a personal research tool that simulates trades. Scores are heuristics,
not advice. Verify any catalyst yourself before risking real money.
