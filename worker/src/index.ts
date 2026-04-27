/**
 * Cloudflare Worker webhook for Pravy's Market Bot.
 *
 * Flow per incoming Telegram update:
 *  1. Respond 200 to Telegram immediately (webhooks must ack within a few
 *     seconds — we use `ctx.waitUntil` to process in the background so
 *     Telegram does not re-deliver).
 *  2. Authorise the chat: group chat id equals TELEGRAM_CHAT_ID, or owner
 *     DM equals TELEGRAM_OWNER_USER_ID.
 *  3. Extract the text, strip the leading @bot mention / preserve the
 *     slash-command verb if present.
 *  4. Cap input length, check + mark rate-limit via Upstash.
 *  5. Stream the Gemini reply into a Telegram message via editMessageText.
 *  6. Append (user_text, model_text) to Upstash chat_history.
 *
 * Secrets required (set via `wrangler secret put`):
 *  - TELEGRAM_BOT_TOKEN
 *  - TELEGRAM_CHAT_ID
 *  - TELEGRAM_OWNER_USER_ID (optional — enables owner DM)
 *  - TELEGRAM_WEBHOOK_SECRET (header token — Telegram sends it, we verify)
 *  - GOOGLE_API_KEY
 *  - UPSTASH_REDIS_REST_URL
 *  - UPSTASH_REDIS_REST_TOKEN
 *  - BOT_USER_ID_SALT
 */

import { HermesAgent } from "./agent";
import { dispatchWorkflow } from "./github";
import { PortfolioStore, buildPanelContext, extractCandidateSymbols } from "./portfolio";
import { PortfolioCommands, parseCommand } from "./portfolio_commands";
import { RedisStore } from "./redis_store";
import { TelegramStream } from "./streaming";
import type { TelegramMessage, TelegramUpdate } from "./telegram";
import { TelegramClient } from "./telegram";

export interface Env {
  TELEGRAM_BOT_TOKEN: string;
  TELEGRAM_CHAT_ID: string;
  TELEGRAM_OWNER_USER_ID?: string;
  TELEGRAM_WEBHOOK_SECRET: string;
  GOOGLE_API_KEY: string;
  UPSTASH_REDIS_REST_URL: string;
  UPSTASH_REDIS_REST_TOKEN: string;
  BOT_USER_ID_SALT: string;
  GOOGLE_AI_DEFAULT_MODEL: string;
  CANSLIM_PLAYBOOK_FILE_ID: string;
  /** `owner/repo` for the GitHub Actions workflows the scheduled handler dispatches. */
  GITHUB_REPO: string;
  /** Ref to dispatch against. Always set via `[vars]` in wrangler.toml. */
  GITHUB_REF: string;
  /** Fine-grained PAT with `actions:write` on {@link Env.GITHUB_REPO}. */
  GITHUB_DISPATCH_TOKEN: string;
}

/**
 * Cron expression → GitHub Actions workflow filename.
 *
 * Cloudflare fires `scheduled()` with the exact cron string from
 * `wrangler.toml`, so we match on the string itself. Keep this table in
 * sync with `wrangler.toml` `[triggers].crons` and the workflow filenames
 * in `.github/workflows/`.
 */
const CRON_TO_WORKFLOW: Record<string, string> = Object.freeze({
  "0 3 * * 2-6": "market-pulse-morning.yml",
  "0 14 * * 7": "weekly-recap.yml",
  "0 14 * * 1": "weekly-top3.yml",
});

const MAX_INPUT_CHARS = 1000;
const RATE_LIMIT_SECONDS = 30;

/**
 * Constant-time string comparison. Plain `===` short-circuits on the
 * first differing byte, leaking the secret one byte at a time over
 * enough timing samples — Workers run on shared edges so this is a
 * realistic threat for the webhook secret.
 */
async function timingSafeEqual(a: string, b: string): Promise<boolean> {
  const enc = new TextEncoder();
  const ab = enc.encode(a);
  const bb = enc.encode(b);
  // Compare a fixed-length digest of both inputs so length differences
  // don't cause early exit and length differences get folded into the diff.
  const da = new Uint8Array(await crypto.subtle.digest("SHA-256", ab));
  const db = new Uint8Array(await crypto.subtle.digest("SHA-256", bb));
  let diff = ab.byteLength === bb.byteLength ? 0 : 1;
  for (let i = 0; i < da.byteLength; i++) diff |= da[i] ^ db[i];
  return diff === 0;
}

function isAuthorisedChat(chatId: number | string, env: Env): boolean {
  const target = String(chatId);
  if (target === env.TELEGRAM_CHAT_ID) return true;
  return !!env.TELEGRAM_OWNER_USER_ID && target === env.TELEGRAM_OWNER_USER_ID;
}

export function extractText(message: TelegramMessage, botUsername: string | null): string | null {
  let text = (message.text ?? "").trim();
  if (!text) return null;

  // Drop leading @mention via entity offset.
  const entities = message.entities ?? [];
  for (const ent of entities) {
    if (ent.type === "mention" && ent.offset === 0) {
      text = text.slice(ent.length).trim();
      break;
    }
  }

  // Strip only the @bot suffix from a slash command; keep the verb.
  if (botUsername && text.startsWith("/")) {
    const idx = text.indexOf(" ");
    const head = idx === -1 ? text : text.slice(0, idx);
    const rest = idx === -1 ? "" : text.slice(idx);
    const suffix = `@${botUsername}`;
    const stripped = head.endsWith(suffix) ? head.slice(0, -suffix.length) : head;
    text = (stripped + rest).trim();
  }
  return text || null;
}

async function handleUpdate(update: TelegramUpdate, env: Env): Promise<void> {
  const message = update.message;
  if (!message) return;
  const chatId = message.chat?.id;
  const userId = message.from?.id;
  if (!chatId || !userId || message.from?.is_bot) return;

  if (!isAuthorisedChat(chatId, env)) {
    console.info("ignoring unauthorised chat_id=", chatId);
    return;
  }

  const telegram = new TelegramClient(env.TELEGRAM_BOT_TOKEN);
  const me = await telegram.getMe();
  const text = extractText(message, me?.username ?? null);
  if (!text) return;

  if (text.length > MAX_INPUT_CHARS) {
    await telegram.sendMessage(
      chatId,
      `Keep messages under ${MAX_INPUT_CHARS} characters, please — try a shorter question.`,
    );
    return;
  }

  const store = new RedisStore({
    url: env.UPSTASH_REDIS_REST_URL,
    token: env.UPSTASH_REDIS_REST_TOKEN,
    userIdSalt: env.BOT_USER_ID_SALT,
  });

  // Slash-command dispatch — short-circuits before rate-limit + Gemini.
  // Recognised commands (e.g. /portfolio, /add, /picks) reply directly via
  // Telegram and skip the agent. Unknown commands fall through to Gemini.
  const parsed = parseCommand(text);
  if (parsed) {
    const portfolioStore = new PortfolioStore(store);
    const commands = new PortfolioCommands(portfolioStore, store);
    const result = await commands.handle(Number(chatId), parsed.command, parsed.args);
    if (result.shouldSkipAgent) {
      try {
        await telegram.sendMessage(chatId, result.replyText);
        console.info("command-handled chat_id=", chatId, "cmd=", parsed.command);
      } catch (exc) {
        console.error("command reply send failed:", (exc as Error).message);
      }
      return;
    }
  }

  if (await store.isRateLimited(userId, RATE_LIMIT_SECONDS)) {
    console.info("rate-limiting user_id=(hashed)");
    return;
  }
  await store.markUser(userId);

  let historyPayload: Array<{ role: "user" | "model"; text: string }> = [];
  try {
    historyPayload = await store.getHistory(chatId);
  } catch (exc) {
    console.warn("history fetch failed — continuing with empty:", (exc as Error).message);
  }

  const agent = new HermesAgent({
    apiKey: env.GOOGLE_API_KEY,
    model: env.GOOGLE_AI_DEFAULT_MODEL || "gemini-2.5-pro",
    ...(env.CANSLIM_PLAYBOOK_FILE_ID ? { playbookFileId: env.CANSLIM_PLAYBOOK_FILE_ID } : {}),
  });

  // Inject cached guru-panel verdicts when the user mentions a symbol.
  // Gemini sees the panel block above the user text and weaves it into
  // the reply — turns "analyse RELIANCE" into a guru-grounded analysis
  // without a tool-call round-trip.
  let enrichedText = text;
  try {
    const symbols = extractCandidateSymbols(text);
    if (symbols.length > 0) {
      const panelContext = await buildPanelContext(symbols, store);
      if (panelContext) {
        enrichedText = panelContext + text;
        console.info(
          "panel-context injected for",
          symbols.length,
          "candidate symbols, payload len=",
          panelContext.length,
        );
      }
    }
  } catch (exc) {
    console.warn("panel context build failed (continuing without):", (exc as Error).message);
  }

  const stream = new TelegramStream(telegram, chatId);

  let final: string;
  try {
    final = await stream.stream(agent.streamReply(enrichedText, historyPayload));
  } catch (exc) {
    try {
      await store.unmarkUser(userId);
    } catch (cleanupExc) {
      console.warn("unmark_user failed during stream-error recovery:", (cleanupExc as Error).message);
    }
    throw exc;
  }

  try {
    await store.appendTurn(chatId, text, final);
  } catch (exc) {
    console.error("history persist failed — next turn loses context:", (exc as Error).message);
  }
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    // Health check for manual GETs.
    if (request.method === "GET") {
      return new Response("pravys-market-bot worker OK", { status: 200 });
    }
    if (request.method !== "POST") {
      return new Response("method not allowed", { status: 405 });
    }

    // Verify Telegram's secret-token header. Telegram sends this exactly
    // when `setWebhook` was called with `secret_token=…`. Constant-time
    // compare to avoid leaking the secret via byte-by-byte timing.
    const provided = request.headers.get("x-telegram-bot-api-secret-token") ?? "";
    if (!(await timingSafeEqual(provided, env.TELEGRAM_WEBHOOK_SECRET))) {
      return new Response("forbidden", { status: 403 });
    }

    let update: TelegramUpdate;
    try {
      update = await request.json();
    } catch {
      return new Response("bad request", { status: 400 });
    }

    // Ack Telegram within the same request — process in the background.
    // Telegram retries non-200 responses, so we MUST always 200 here even
    // if downstream handling throws. The catch ships an in-chat error so
    // the user isn't staring at silence when something fails before the
    // streaming placeholder is even sent.
    ctx.waitUntil(
      handleUpdate(update, env).catch(async (exc: unknown) => {
        console.error("handler failed for update_id=", update.update_id, exc);
        try {
          const chatId = update.message?.chat?.id;
          if (chatId && isAuthorisedChat(chatId, env)) {
            const tg = new TelegramClient(env.TELEGRAM_BOT_TOKEN);
            await tg.sendMessage(
              chatId,
              "⚠️ Something broke on my end, mate — tag Pravy if this keeps happening.",
            );
          }
        } catch (notifyExc) {
          console.error("failed to notify user of handler failure:", notifyExc);
        }
      }),
    );
    return new Response("ok", { status: 200 });
  },

  /**
   * Cloudflare cron trigger. Fires at the exact times listed under
   * `[triggers].crons` in `wrangler.toml`. We use CF cron (<1 min drift)
   * to dispatch GitHub Actions workflows instead of GitHub's native
   * scheduled trigger, which routinely runs 15–60 min late at top-of-hour.
   *
   * Any error here must surface in Worker logs — we intentionally DO NOT
   * swallow dispatch failures, because a silent failure means no Telegram
   * digest and no user-visible signal.
   */
  async scheduled(event: ScheduledController, env: Env, _ctx: ExecutionContext): Promise<void> {
    const workflow = CRON_TO_WORKFLOW[event.cron];
    if (!workflow) {
      throw new Error(`scheduled: no workflow mapped for cron "${event.cron}"`);
    }
    // Awaiting directly (rather than wrapping in ctx.waitUntil) is load-bearing:
    // only a rejection on the handler's own return value causes Cloudflare to
    // mark the scheduled invocation as failed. waitUntil rejections become
    // unhandled promise rejections — the dashboard would show green even when
    // dispatch errored, defeating the entire observability story.
    try {
      await dispatchWorkflow({
        repo: env.GITHUB_REPO,
        workflow,
        ref: env.GITHUB_REF,
        token: env.GITHUB_DISPATCH_TOKEN,
      });
      console.info("scheduled: dispatched", workflow, "for cron", event.cron);
    } catch (exc) {
      console.error("scheduled: dispatch failed", workflow, exc);
      throw exc;
    }
  },
} satisfies ExportedHandler<Env>;
