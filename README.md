# Pravy's Market Bot

CAN SLIM–driven NSE market pulse delivered to a Telegram group — **morning briefing**, **evening recap**, and a **weekly top-3 picks** digest. Built on the [MarketSmith India CAN SLIM Playbook](./TheCAN-SLIM®-Playbook.pdf) methodology.

## What it sends

| When (Asia/Taipei) | What |
| --- | --- |
| **11:00 Mon–Fri** (08:30 IST, before NSE open) | Morning pulse — market phase, top 10 CAN SLIM scorers overnight, commodities + USD/INR, Gemini-grounded overnight cues |
| **18:15 Mon–Fri** (15:45 IST, 15 min after NSE close) | Evening recap — index movements, top 5 gainers/losers with volume multiples, Gemini narrative |
| **22:00 Sun** | Weekly Top 3 — stocks meeting 6+/7 CAN SLIM letters with per-letter breakdown, Gemini rationale grounded on 7-day news via Google CSE, plus the risk-management rules footer |

All runs skip **NSE holidays** (detected via `nselib`) and weekends.

## CAN SLIM thresholds (from the playbook)

| Letter | Rule | Data source |
| --- | --- | --- |
| **C** | Current quarterly EPS YoY growth ≥ 25% | yfinance `quarterly_income_stmt` |
| **A** | 3-year annual EPS CAGR ≥ 20% | yfinance `income_stmt` |
| **N** | Within 15% of 52-week high (limited overhead supply) | yfinance 252-day OHLC |
| **S** | Today's volume ≥ 1.4× 50-day average (the playbook's +40% rule) | yfinance daily |
| **L** | RS Rating ≥ 80 (percentile 12-month return across NSE universe) | computed |
| **I** | FII + DII net inflow positive over last 5 days | `nselib.fii_dii_trading_activity` |
| **M** | Market phase = Confirmed Uptrend (Nifty above 50/200 DMA + 5-day trend up) | computed on `^NSEI` |

The binary score (0–7) is the primary rank; the tiebreak sums each letter's continuous magnitude.

## Market phases (MarketSmith India 4-state classifier)

| Phase | Signals | Action suggested |
| --- | --- | --- |
| Confirmed Uptrend | above 50 + 200 DMA, 5d trend up | buy aggressively |
| Uptrend Under Pressure | above 200 DMA, 50 DMA or 5d trend weak | stay cautious |
| Rally Attempt | below both DMAs but 5d trend up | test small positions |
| Downtrend | below both DMAs, 5d flat/down | reduce exposure |

## Universe

- **Nifty 500** constituents (via `nselib.nifty500_equity_list`)
- **Commodity ETFs** — `GOLDBEES`, `AXISGOLD`, `GOLDSHARE`, `HDFCMFGETF`, `KOTAKGOLD`, `SILVERBEES`
- **Commodity + FX tracker** (displayed in every digest, not scored) — Gold USD/oz, Silver, Crude, USD/INR

## Local development

```bash
uv sync
cp .env.example .env   # fill in values
uv run pytest          # 56+ tests
uv run ruff check .
uv run ruff format --check .

# Run a digest job locally
set -a; source .env; set +a
uv run python -m jobs.morning_pulse
```

## Deployment

- Runs on **GitHub Actions** cron — nothing else to operate
- Secrets required: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GOOGLE_API_KEY`, optionally `GOOGLE_SEARCH_API_KEY` + `GOOGLE_CSE_ID` for news grounding
- On-demand `@mention` responder + watchlist commands land in **Phase 2** on Fly.io (a long-polling bot service that imports the same `core/` library)

## Phase 2 (planned)

- Fly.io long-polling bot (`aiogram` v3) for:
  - `@pravys_market_bot` → top 5 CAN SLIM picks right now
  - `/today RELIANCE` → full CAN SLIM breakdown of one ticker
  - `/add /remove /list` watchlist management
- 15-minute breakout scanner during NSE hours per user watchlist

## Phase 3 (nice-to-have)

- EPS Rating and SMR Rating composites (needs screener.in or paid feed)
- 197 industry-groups ranking (top 40 bonus flag)
- Accumulation/Distribution A–E rating
- Intraday quotes via Upstox / Zerodha Kite Connect for real-time `@mention` responses

## What this is *not*

This is not MarketSmith India. It's an open-source approximation of their methodology using free public data. It replicates the scoring framework, the rhythm (pre-open / post-close / weekly), and the risk rules. It does **not** replicate their proprietary EPS Rating, SMR Rating, 197-group list, analyst columns, or tick-level intraday feed.

**Educational signals, not investment advice. Do your own research.**
