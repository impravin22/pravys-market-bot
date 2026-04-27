/**
 * Free Yahoo Finance OHLCV fetcher for the Worker.
 *
 * Endpoint: query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}
 * Returns daily OHLCV arrays — same shape as `nse_data.fetch_history` on
 * the Python side. Worker uses this for /sells (per-holding sell-rule eval).
 *
 * Honest caveats:
 * - Free, unauthenticated. No SLA. May rate-limit aggressive callers.
 * - Indian symbols use suffix `.NS` (NSE) or `.BO` (BSE) — same as yfinance.
 * - Returns `null` on any non-200 / parse failure. Caller decides.
 */

const BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart";
const HTTP_TIMEOUT_MS = 8000;

export interface OhlcvBar {
  /** Close timestamp in seconds since epoch (Yahoo provides this). */
  timestamp: number;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
}

export interface OhlcvHistory {
  symbol: string;
  bars: OhlcvBar[];
}

interface YahooChartResponse {
  chart: {
    result?: Array<{
      timestamp?: number[];
      indicators?: {
        quote?: Array<{
          open?: Array<number | null>;
          high?: Array<number | null>;
          low?: Array<number | null>;
          close?: Array<number | null>;
          volume?: Array<number | null>;
        }>;
      };
    }>;
    error?: { code?: string; description?: string };
  };
}

/**
 * Fetch up to one year of daily OHLCV. Returns `null` on any failure.
 *
 * `range` follows Yahoo's accepted set: 1d / 5d / 1mo / 3mo / 6mo / 1y / 2y / 5y / max.
 */
export async function fetchHistory(
  symbol: string,
  options: { range?: string; fetchImpl?: typeof fetch } = {},
): Promise<OhlcvHistory | null> {
  const range = options.range ?? "1y";
  const url = `${BASE_URL}/${encodeURIComponent(symbol)}?range=${range}&interval=1d&includePrePost=false`;
  const fetchImpl = options.fetchImpl ?? fetch;

  let resp: Response;
  try {
    resp = await fetchImpl(url, {
      method: "GET",
      headers: {
        Accept: "application/json",
        "User-Agent":
          "pravys-market-bot/0.1 (+https://github.com/impravin22/pravys-market-bot)",
      },
      signal: AbortSignal.timeout(HTTP_TIMEOUT_MS),
    });
  } catch (exc) {
    console.warn("yahoo fetch transport error for", symbol, (exc as Error).message);
    return null;
  }
  if (!resp.ok) {
    console.warn("yahoo fetch non-200 for", symbol, resp.status);
    return null;
  }
  let payload: YahooChartResponse;
  try {
    payload = (await resp.json()) as YahooChartResponse;
  } catch (exc) {
    console.warn("yahoo fetch non-JSON for", symbol, (exc as Error).message);
    return null;
  }
  const result = payload.chart?.result?.[0];
  if (!result) return null;
  const ts = result.timestamp ?? [];
  const quote = result.indicators?.quote?.[0];
  if (!quote) return null;

  const bars: OhlcvBar[] = ts.map((t, i) => ({
    timestamp: t,
    open: quote.open?.[i] ?? null,
    high: quote.high?.[i] ?? null,
    low: quote.low?.[i] ?? null,
    close: quote.close?.[i] ?? null,
    volume: quote.volume?.[i] ?? null,
  }));
  return { symbol, bars };
}

/** Latest non-null close from the history. Returns null if no usable bars. */
export function latestClose(history: OhlcvHistory): number | null {
  for (let i = history.bars.length - 1; i >= 0; i--) {
    const c = history.bars[i].close;
    if (typeof c === "number" && Number.isFinite(c)) return c;
  }
  return null;
}
