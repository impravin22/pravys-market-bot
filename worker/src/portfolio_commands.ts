/**
 * Slash-command parser + dispatcher for the Telegram Worker.
 *
 * Recognised commands short-circuit before Gemini and reply directly via
 * Telegram. Unknown commands fall through (`shouldSkipAgent: false`) so
 * free-form `/X` queries still go to Gemini.
 *
 * TS port of `bot/handlers/portfolio_commands.py`. Adds `/picks` reading
 * from the same Upstash key the Python cron writes to.
 */

import {
  CachedPicks,
  Holding,
  Portfolio,
  PortfolioStore,
  investedCapital,
  makeHolding,
  readPicksCache,
} from "./portfolio";
import { RedisStore } from "./redis_store";

export interface CommandResult {
  replyText: string;
  shouldSkipAgent: boolean;
}

export function parseCommand(text: string): { command: string; args: string[] } | null {
  const stripped = text.trim();
  if (!stripped || !stripped.startsWith("/")) return null;
  const parts = stripped.slice(1).split(/\s+/).filter((p) => p.length > 0);
  if (parts.length === 0) return null;
  return { command: parts[0].toLowerCase(), args: parts.slice(1) };
}

const HELP_TEXT = [
  "Portfolio commands:",
  "  /portfolio — list your holdings",
  "  /add SYMBOL QTY PRICE [YYYY-MM-DD] — add a holding",
  "  /remove SYMBOL — remove a holding",
  "  /picks — show today's top buy candidates (cached)",
  "  /clear CONFIRM — wipe portfolio (requires the word CONFIRM)",
  "  /help — this message",
].join("\n");

export class PortfolioCommands {
  constructor(
    private readonly store: PortfolioStore,
    private readonly redis: RedisStore,
    private readonly today: () => string = () => new Date().toISOString().slice(0, 10),
  ) {}

  async handle(chatId: number, command: string, args: string[]): Promise<CommandResult> {
    switch (command) {
      case "help":
      case "start":
        return { replyText: HELP_TEXT, shouldSkipAgent: true };
      case "portfolio":
        return this.cmdPortfolio(chatId);
      case "add":
        return this.cmdAdd(chatId, args);
      case "remove":
        return this.cmdRemove(chatId, args);
      case "clear":
        return this.cmdClear(chatId, args);
      case "picks":
        return this.cmdPicks();
      default:
        return { replyText: "", shouldSkipAgent: false };
    }
  }

  // -------------------- /portfolio --------------------

  private async cmdPortfolio(chatId: number): Promise<CommandResult> {
    const portfolio = await this.store.get(chatId);
    if (portfolio.holdings.length === 0) {
      return {
        replyText: "No holdings yet. Add one with `/add SYMBOL QTY PRICE`.",
        shouldSkipAgent: true,
      };
    }
    const lines = [`Your portfolio (${portfolio.holdings.length} positions):`];
    for (const h of portfolio.holdings) {
      lines.push(
        `• ${h.symbol} — qty ${h.qty} @ ₹${h.buy_price.toFixed(2)} ` +
          `(bought ${h.buy_date}) · Stop ₹${h.stop_loss.toFixed(2)}`,
      );
    }
    lines.push("");
    lines.push(`Invested capital: ₹${investedCapital(portfolio).toFixed(2)}`);
    return { replyText: lines.join("\n"), shouldSkipAgent: true };
  }

  // -------------------- /add --------------------

  private async cmdAdd(chatId: number, args: string[]): Promise<CommandResult> {
    if (args.length !== 3 && args.length !== 4) {
      return {
        replyText:
          "Usage: /add SYMBOL QTY PRICE [YYYY-MM-DD]\n" +
          "Example: /add RELIANCE 50 2400 2026-04-21",
        shouldSkipAgent: true,
      };
    }
    const [symbolRaw, qtyRaw, priceRaw, dateRaw] = args;
    const qty = Number.parseInt(qtyRaw, 10);
    if (!Number.isFinite(qty) || String(qty) !== qtyRaw) {
      return { replyText: `qty must be a whole number, got '${qtyRaw}'.`, shouldSkipAgent: true };
    }
    if (qty <= 0) {
      return { replyText: "qty must be positive.", shouldSkipAgent: true };
    }
    const price = Number.parseFloat(priceRaw);
    if (!Number.isFinite(price)) {
      return { replyText: `price must be a number, got '${priceRaw}'.`, shouldSkipAgent: true };
    }
    if (price <= 0) {
      return { replyText: "price must be positive.", shouldSkipAgent: true };
    }
    let buyDate: string;
    if (dateRaw === undefined) {
      buyDate = this.today();
    } else if (!/^\d{4}-\d{2}-\d{2}$/.test(dateRaw) || Number.isNaN(Date.parse(dateRaw))) {
      return {
        replyText: `date must be YYYY-MM-DD, got '${dateRaw}'.`,
        shouldSkipAgent: true,
      };
    } else {
      buyDate = dateRaw;
    }

    const symbol = normaliseSymbol(symbolRaw);
    const holding: Holding = makeHolding({ symbol, qty, buy_price: price, buy_date: buyDate });
    await this.store.add(chatId, holding);
    return {
      replyText:
        `Added ${symbol}: qty ${qty} @ ₹${price.toFixed(2)} on ${buyDate}.\n` +
        `Default stop-loss ₹${holding.stop_loss.toFixed(2)} (7% below buy).`,
      shouldSkipAgent: true,
    };
  }

  // -------------------- /remove --------------------

  private async cmdRemove(chatId: number, args: string[]): Promise<CommandResult> {
    if (args.length !== 1) {
      return {
        replyText: "Usage: /remove SYMBOL\nExample: /remove RELIANCE",
        shouldSkipAgent: true,
      };
    }
    const symbol = normaliseSymbol(args[0]);
    const removed = await this.store.remove(chatId, symbol);
    if (!removed) {
      return {
        replyText: `${symbol} is not in your portfolio. Use /portfolio to list current holdings.`,
        shouldSkipAgent: true,
      };
    }
    return {
      replyText: `Removed ${removed.symbol} (qty ${removed.qty} @ ₹${removed.buy_price.toFixed(2)}).`,
      shouldSkipAgent: true,
    };
  }

  // -------------------- /clear --------------------

  private async cmdClear(chatId: number, args: string[]): Promise<CommandResult> {
    if (args.length !== 1 || args[0] !== "CONFIRM") {
      return {
        replyText: "Destructive. Send `/clear CONFIRM` to wipe your portfolio.",
        shouldSkipAgent: true,
      };
    }
    await this.store.clear(chatId);
    return { replyText: "Portfolio cleared.", shouldSkipAgent: true };
  }

  // -------------------- /picks --------------------

  private async cmdPicks(): Promise<CommandResult> {
    const cached: CachedPicks | null = await readPicksCache(this.redis);
    if (!cached || cached.picks.length === 0) {
      return {
        replyText:
          "No picks computed yet. The morning cron writes them daily — " +
          "or ask Pravy to seed via `uv run python -m jobs.daily_picks_job`.",
        shouldSkipAgent: true,
      };
    }
    const computedDate = cached.computed_at.slice(0, 16).replace("T", " ");
    const lines = [`Top picks (computed ${computedDate} UTC):`];
    for (const p of cached.picks.slice(0, 5)) {
      const endorsers = p.endorsing_codes.length > 0 ? p.endorsing_codes.join(", ") : "—";
      const s = p.endorsement_count !== 1 ? "s" : "";
      lines.push(
        `• ${p.symbol} — composite ${p.composite_rating.toFixed(0)}/99 · ` +
          `${p.endorsement_count} guru${s} (${endorsers})`,
      );
      if (p.fundamentals_summary) {
        lines.push(`  ${p.fundamentals_summary}`);
      }
    }
    return { replyText: lines.join("\n"), shouldSkipAgent: true };
  }
}

// -----------------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------------

export function normaliseSymbol(raw: string): string {
  const upper = raw.trim().toUpperCase();
  if (upper.endsWith(".NS") || upper.endsWith(".BO")) return upper;
  return `${upper}.NS`;
}
