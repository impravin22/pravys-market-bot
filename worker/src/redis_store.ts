/**
 * Upstash Redis REST client for the Worker — a TS port of bot/redis_store.py.
 *
 * Why we keep the same shape:
 *  - `telegram:offset` is only used by the Python cron; the Worker does not
 *    touch it. But reusing the rate-limit / chat-history keys means Python
 *    and the Worker can share state (e.g. digests written by Python are
 *    visible to the Worker, and conversation memory carries over across
 *    any transition).
 *  - user_ids and chat_ids are HMAC-SHA256 hashed with a per-deployment
 *    salt before they become Redis keys, to keep raw Telegram IDs out of
 *    Upstash's keyspace.
 *
 * Every command is funnelled through `_call` so tests can swap the fetch
 * implementation without monkey-patching. Transport errors and non-JSON
 * bodies are normalised into a single `RuntimeError`-style Error.
 */

export interface RedisConfig {
  url: string;
  token: string;
  userIdSalt: string;
  fetch?: typeof fetch;
}

export const RATE_LIMIT_SECONDS = 30;
export const CHAT_HISTORY_LIMIT = 10;
export const CHAT_HISTORY_TTL_SECONDS = 7 * 24 * 3600;

export interface HistoryTurn {
  role: "user" | "model";
  text: string;
}

async function hashUserId(userId: string | number, salt: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(salt),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, encoder.encode(String(userId)));
  const bytes = new Uint8Array(sig);
  const hex = Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  // First 16 hex chars mirror the Python side, so keys are interoperable.
  return hex.slice(0, 16);
}

export class RedisStore {
  private readonly fetchImpl: typeof fetch;

  constructor(private readonly config: RedisConfig) {
    // Cloudflare Workers throws `TypeError: Illegal invocation` if native
    // `fetch` is called off an instance property without its global `this`.
    // Wrap in an arrow so the receiver is always the module scope, and any
    // injected mock (e.g. tests) retains its own binding semantics.
    const impl = config.fetch ?? fetch;
    this.fetchImpl = (input, init) => impl(input, init);
  }

  private async call(...args: string[]): Promise<unknown> {
    let resp: Response;
    try {
      resp = await this.fetchImpl(this.config.url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${this.config.token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(args),
      });
    } catch (exc) {
      throw new Error(`Upstash ${args[0]} transport error: ${(exc as Error).message}`);
    }
    if (resp.status >= 500) {
      const body = (await resp.text()).slice(0, 200);
      throw new Error(`Upstash ${args[0]} 5xx status=${resp.status} body=${body}`);
    }
    let payload: unknown;
    try {
      payload = await resp.json();
    } catch (exc) {
      const body = (await resp.text().catch(() => "")).slice(0, 200);
      throw new Error(
        `Upstash returned non-JSON status=${resp.status} body=${body}: ${(exc as Error).message}`,
      );
    }
    if (payload && typeof payload === "object" && "error" in payload) {
      throw new Error(`Upstash ${args[0]} failed: ${(payload as { error: string }).error}`);
    }
    return (payload as { result?: unknown }).result;
  }

  // -------------------- rate limit --------------------

  private async rateLimitKey(userId: string | number): Promise<string> {
    return `rate_limit:${await hashUserId(userId, this.config.userIdSalt)}`;
  }

  async isRateLimited(userId: string | number, seconds = RATE_LIMIT_SECONDS): Promise<boolean> {
    const raw = await this.call("GET", await this.rateLimitKey(userId));
    if (typeof raw !== "string") return false;
    const last = new Date(raw).getTime();
    if (Number.isNaN(last)) return false;
    return Date.now() - last < seconds * 1000;
  }

  async markUser(userId: string | number): Promise<void> {
    await this.call(
      "SET",
      await this.rateLimitKey(userId),
      new Date().toISOString(),
      "EX",
      String(RATE_LIMIT_SECONDS),
    );
  }

  async unmarkUser(userId: string | number): Promise<void> {
    await this.call("DEL", await this.rateLimitKey(userId));
  }

  // -------------------- chat history --------------------

  private async historyKey(chatId: string | number): Promise<string> {
    return `chat_history:${await hashUserId(chatId, this.config.userIdSalt)}`;
  }

  async getHistory(chatId: string | number): Promise<HistoryTurn[]> {
    const key = await this.historyKey(chatId);
    const raw = await this.call("GET", key);
    if (typeof raw !== "string") return [];
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch (exc) {
      console.error(
        `chat history JSON corrupt at ${key} (len=${raw.length}); self-healing by deleting:`,
        (exc as Error).message,
      );
      // Self-heal: drop the corrupt key so the next turn starts fresh.
      await this.call("DEL", key).catch((delExc) =>
        console.error("failed to delete corrupt history key:", (delExc as Error).message),
      );
      return [];
    }
    if (!Array.isArray(parsed)) {
      console.error(`chat history at ${key} is not an array; self-healing by deleting`);
      await this.call("DEL", key).catch(() => {});
      return [];
    }
    const valid = parsed.filter(
      (t): t is HistoryTurn =>
        !!t &&
        typeof t === "object" &&
        "role" in t &&
        "text" in t &&
        typeof (t as { text: unknown }).text === "string",
    );
    if (valid.length !== parsed.length) {
      console.warn(
        `dropped ${parsed.length - valid.length}/${parsed.length} malformed history turns at ${key}`,
      );
    }
    return valid;
  }

  async appendTurn(
    chatId: string | number,
    userText: string,
    botText: string,
    limit = CHAT_HISTORY_LIMIT,
  ): Promise<void> {
    const history = await this.getHistory(chatId);
    history.push({ role: "user", text: userText });
    history.push({ role: "model", text: botText });
    const trimmed = history.length > 2 * limit ? history.slice(-2 * limit) : history;
    await this.call(
      "SET",
      await this.historyKey(chatId),
      JSON.stringify(trimmed),
      "EX",
      String(CHAT_HISTORY_TTL_SECONDS),
    );
  }
}
