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
}

const MAX_INPUT_CHARS = 1000;
const RATE_LIMIT_SECONDS = 30;

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

  const stream = new TelegramStream(telegram, chatId);

  let final: string;
  try {
    final = await stream.stream(agent.streamReply(text, historyPayload));
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
    // when `setWebhook` was called with `secret_token=…`.
    const provided = request.headers.get("x-telegram-bot-api-secret-token");
    if (provided !== env.TELEGRAM_WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }

    let update: TelegramUpdate;
    try {
      update = await request.json();
    } catch {
      return new Response("bad request", { status: 400 });
    }

    // Ack Telegram within the same request — process in the background.
    ctx.waitUntil(
      handleUpdate(update, env).catch((exc: unknown) => {
        console.error("handler failed for update_id=", update.update_id, exc);
      }),
    );
    return new Response("ok", { status: 200 });
  },
} satisfies ExportedHandler<Env>;
