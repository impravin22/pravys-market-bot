/**
 * Portfolio model + Upstash-backed store for the Cloudflare Worker.
 *
 * TS port of `core/portfolio.py`. Same Redis schema (`portfolio:{hashed_chat_id}`)
 * so the Python morning_pulse / evening_recap jobs and the Worker share state.
 */

import type { RedisStore } from "./redis_store";

export const DEFAULT_STOP_LOSS_PCT = 0.07;
export const KEY_PREFIX = "portfolio:";
export const PORTFOLIO_TTL_SECONDS = 60 * 60 * 24 * 365;

export interface Holding {
  symbol: string;
  qty: number;
  buy_price: number;
  buy_date: string; // ISO YYYY-MM-DD
  source_guru?: string | null;
  pivot_price?: number | null;
  stop_loss: number;
  target_price?: number | null;
  notes: string;
}

export interface Portfolio {
  chat_id: number;
  holdings: Holding[];
  cash_remaining: number;
  last_updated: string; // ISO datetime
}

export function makeHolding(input: {
  symbol: string;
  qty: number;
  buy_price: number;
  buy_date: string;
  source_guru?: string | null;
  pivot_price?: number | null;
  stop_loss?: number | null;
  target_price?: number | null;
  notes?: string;
}): Holding {
  const stop = input.stop_loss && input.stop_loss > 0
    ? input.stop_loss
    : Math.round(input.buy_price * (1.0 - DEFAULT_STOP_LOSS_PCT) * 100) / 100;
  return {
    symbol: input.symbol,
    qty: input.qty,
    buy_price: input.buy_price,
    buy_date: input.buy_date,
    source_guru: input.source_guru ?? null,
    pivot_price: input.pivot_price ?? null,
    stop_loss: stop,
    target_price: input.target_price ?? null,
    notes: input.notes ?? "",
  };
}

export function pnlPct(h: Holding, currentPrice: number): number {
  if (h.buy_price === 0) return 0;
  return Math.round((currentPrice / h.buy_price - 1.0) * 100.0 * 100) / 100;
}

export function pnlValue(h: Holding, currentPrice: number): number {
  return Math.round((currentPrice - h.buy_price) * h.qty * 100) / 100;
}

export function investedCapital(p: Portfolio): number {
  const sum = p.holdings.reduce((acc, h) => acc + h.qty * h.buy_price, 0);
  return Math.round(sum * 100) / 100;
}

export class PortfolioStore {
  constructor(private readonly redis: RedisStore) {}

  private async key(chatId: number): Promise<string> {
    return `${KEY_PREFIX}${await this.redis.hashId(chatId)}`;
  }

  async get(chatId: number): Promise<Portfolio> {
    const raw = await this.redis.command("GET", await this.key(chatId));
    if (typeof raw !== "string") return emptyPortfolio(chatId);
    try {
      const data = JSON.parse(raw) as Partial<Portfolio>;
      return {
        chat_id: typeof data.chat_id === "number" ? data.chat_id : chatId,
        holdings: Array.isArray(data.holdings) ? data.holdings.map(coerceHolding) : [],
        cash_remaining: typeof data.cash_remaining === "number" ? data.cash_remaining : 0,
        last_updated: typeof data.last_updated === "string" ? data.last_updated : new Date().toISOString(),
      };
    } catch (exc) {
      console.warn(
        `portfolio JSON corrupt for chat_id=${chatId}; resetting to empty:`,
        (exc as Error).message,
      );
      return emptyPortfolio(chatId);
    }
  }

  async add(chatId: number, holding: Holding): Promise<Portfolio> {
    const current = await this.get(chatId);
    const updated: Portfolio = {
      ...current,
      holdings: [...current.holdings, holding],
      last_updated: new Date().toISOString(),
    };
    await this.save(updated);
    return updated;
  }

  async remove(chatId: number, symbol: string): Promise<Holding | null> {
    const current = await this.get(chatId);
    const target = current.holdings.find((h) => h.symbol === symbol);
    if (!target) return null;
    const kept = current.holdings.filter((h) => h.symbol !== symbol);
    await this.save({ ...current, holdings: kept, last_updated: new Date().toISOString() });
    return target;
  }

  async clear(chatId: number): Promise<void> {
    await this.save({ ...emptyPortfolio(chatId), last_updated: new Date().toISOString() });
  }

  private async save(portfolio: Portfolio): Promise<void> {
    await this.redis.command(
      "SET",
      await this.key(portfolio.chat_id),
      JSON.stringify(portfolio),
      "EX",
      String(PORTFOLIO_TTL_SECONDS),
    );
  }
}

function emptyPortfolio(chatId: number): Portfolio {
  return {
    chat_id: chatId,
    holdings: [],
    cash_remaining: 0,
    last_updated: new Date().toISOString(),
  };
}

function coerceHolding(raw: unknown): Holding {
  const r = raw as Partial<Holding>;
  return {
    symbol: String(r.symbol ?? ""),
    qty: Number(r.qty ?? 0),
    buy_price: Number(r.buy_price ?? 0),
    buy_date: String(r.buy_date ?? ""),
    source_guru: r.source_guru ?? null,
    pivot_price: typeof r.pivot_price === "number" ? r.pivot_price : null,
    stop_loss: Number(r.stop_loss ?? 0),
    target_price: typeof r.target_price === "number" ? r.target_price : null,
    notes: String(r.notes ?? ""),
  };
}

// -----------------------------------------------------------------------------
// Picks cache reader — `picks:latest` populated by the Python morning cron.
// -----------------------------------------------------------------------------

export interface CachedPick {
  symbol: string;
  composite_rating: number;
  endorsement_count: number;
  endorsing_codes: string[];
  fundamentals_summary?: string;
}

export interface CachedPicks {
  picks: CachedPick[];
  computed_at: string; // ISO datetime
}

export const PICKS_CACHE_KEY = "picks:latest";

export async function readPicksCache(redis: RedisStore): Promise<CachedPicks | null> {
  const raw = await redis.command("GET", PICKS_CACHE_KEY);
  if (typeof raw !== "string") return null;
  try {
    const data = JSON.parse(raw) as Partial<CachedPicks>;
    if (!Array.isArray(data.picks) || typeof data.computed_at !== "string") return null;
    return {
      picks: data.picks as CachedPick[],
      computed_at: data.computed_at,
    };
  } catch (exc) {
    console.warn("picks cache corrupt JSON:", (exc as Error).message);
    return null;
  }
}
