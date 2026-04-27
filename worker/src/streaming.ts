/**
 * Stream Gemini text chunks into a live Telegram message via editMessageText.
 *
 * Port of bot/streaming.py. Design decisions:
 *  - Send a placeholder, capture its message_id, edit repeatedly.
 *  - Throttle to ~1 edit/sec/chat to stay inside Telegram's rate limits.
 *  - When the streamed reply exceeds the 4096-char Telegram cap, finalise
 *    the current message at a safe newline boundary and continue streaming
 *    into a fresh sendMessage so long replies span 2-3 messages instead
 *    of getting truncated mid-thought.
 *  - On "can't parse entities" fall back to one plain-text edit.
 *  - Non-parse 4xx bubble up so the caller can release the rate-limit slot.
 */

import { markdownToHtml } from "./markdown_to_html";
import { TelegramClient } from "./telegram";

export const PLACEHOLDER_TEXT = "⏳ Give me a sec, mate…";
export const CONTINUATION_PLACEHOLDER = "⏳ … continued, mate …";
export const EDIT_INTERVAL_MS = 1200;
export const TELEGRAM_MAX_CHARS = 4096;
/**
 * Per-message safety cap. Below the hard 4096 limit so HTML expansion
 * (e.g. **bold** → <b>...</b>) and continuation footers don't push us over.
 */
export const SAFE_PER_MESSAGE_CHARS = 3800;
/**
 * Window before the cap in which we look for a clean newline to split on.
 * 600 chars covers a few paragraphs of a typical Gemini reply.
 */
export const SPLIT_LOOKBACK_CHARS = 600;

export class TelegramStream {
  private messageId: number | null = null;
  private lastHtml: string | null = null;
  private lastEditAt = 0;

  constructor(
    private readonly telegram: TelegramClient,
    private readonly chatId: number | string,
    private readonly placeholder: string = PLACEHOLDER_TEXT,
  ) {}

  private async start(): Promise<void> {
    this.messageId = await this.telegram.sendMessage(this.chatId, this.placeholder);
  }

  private async editOnce(text: string): Promise<void> {
    if (this.messageId == null) throw new Error("edit called before start");
    const rendered = markdownToHtml(text);
    if (rendered === this.lastHtml) return;

    const result = await this.telegram.editMessageText(this.chatId, this.messageId, rendered, {
      parseMode: "HTML",
    });
    if (result.ok) {
      this.lastHtml = rendered;
      return;
    }

    const description = (result.description ?? "").toLowerCase();
    if (description.includes("not modified")) {
      this.lastHtml = rendered;
      return;
    }
    if (description.includes("can't parse entities")) {
      // Retry once as plain text (parse_mode=null).
      const plain = await this.telegram.editMessageText(this.chatId, this.messageId, text, {});
      if (!plain.ok) {
        throw new Error(
          `editMessageText plain fallback failed: ${plain.description ?? "unknown"}`,
        );
      }
      this.lastHtml = null; // plain override — next edit must re-render
      return;
    }
    // Recovery: original placeholder vanished (admin deleted it, message
    // is too old to edit, etc). Send a fresh message and continue editing
    // that one instead. The user still sees the reply.
    if (
      description.includes("message to edit not found") ||
      description.includes("message can't be edited") ||
      description.includes("message_id_invalid")
    ) {
      console.warn("placeholder vanished; sending fresh message", {
        chatId: this.chatId,
        oldMessageId: this.messageId,
      });
      this.messageId = await this.telegram.sendMessage(this.chatId, text);
      this.lastHtml = null;
      return;
    }
    // Bot was kicked / chat blocked / user deactivated — no point retrying.
    if (
      description.includes("bot was blocked") ||
      description.includes("user is deactivated") ||
      description.includes("chat not found")
    ) {
      throw new Error(`editMessageText terminal: ${result.description ?? "unknown"}`);
    }
    // Flood control — propagate so caller can back off explicitly.
    if (description.includes("too many requests") || description.includes("retry after")) {
      throw new Error(`editMessageText flood: ${result.description ?? "unknown"}`);
    }
    throw new Error(`editMessageText failed: ${result.description ?? "unknown"}`);
  }

  private async safeEdit(text: string): Promise<void> {
    try {
      await this.editOnce(text);
    } catch (exc) {
      console.info("intermediate edit failed (non-fatal):", (exc as Error).message);
    }
  }

  /**
   * Begin a continuation message — finalises the current edited message
   * (without a "continued" footer; readers see one clean message, then
   * the next) and opens a fresh placeholder for the tail of the reply.
   */
  private async startContinuation(): Promise<void> {
    this.messageId = await this.telegram.sendMessage(
      this.chatId,
      CONTINUATION_PLACEHOLDER,
    );
    this.lastHtml = null;
    this.lastEditAt = 0;
  }

  /**
   * Consume the async chunk iterator, driving edits. Returns the
   * concatenation of every part that was sent — useful for chat history
   * persistence (so the user's next turn sees the full prior reply).
   *
   * When the running buffer exceeds `SAFE_PER_MESSAGE_CHARS`, the current
   * message is committed at the last newline within the lookback window
   * (or a hard split if nothing clean is available) and a fresh
   * sendMessage starts the next part.
   */
  async stream(chunks: AsyncIterable<string>): Promise<string> {
    await this.start();
    let buffer = "";
    let committedTotal = "";

    for await (const piece of chunks) {
      buffer += piece;

      // Spill over: commit current message at a safe boundary, continue
      // streaming the tail into a fresh message. We loop because a single
      // chunk can be very large (rare but possible).
      while (buffer.length > SAFE_PER_MESSAGE_CHARS) {
        const splitAt = findSafeSplit(buffer, SAFE_PER_MESSAGE_CHARS);
        const head = buffer.slice(0, splitAt).trimEnd();
        const tail = buffer.slice(splitAt);
        if (head.length > 0) {
          await this.editOnce(head);
          committedTotal += (committedTotal.length > 0 ? "\n\n" : "") + head;
        }
        await this.startContinuation();
        buffer = tail.trimStart();
      }

      const now = Date.now();
      if (now - this.lastEditAt >= EDIT_INTERVAL_MS) {
        await this.safeEdit(buffer);
        this.lastEditAt = now;
      }
    }

    const final = buffer.trim();
    if (final.length > 0) {
      await this.editOnce(final);
      committedTotal += (committedTotal.length > 0 ? "\n\n" : "") + final;
    }
    return committedTotal;
  }
}

/**
 * Pick a split index inside ``[max - SPLIT_LOOKBACK_CHARS, max]``: prefer
 * the position right after the last newline; fall back to a hard cut at
 * ``max`` if the window has no newline.
 */
export function findSafeSplit(text: string, max: number): number {
  if (text.length <= max) return text.length;
  const windowStart = Math.max(0, max - SPLIT_LOOKBACK_CHARS);
  const window = text.slice(windowStart, max);
  const lastNewline = window.lastIndexOf("\n");
  if (lastNewline !== -1) return windowStart + lastNewline + 1;
  return max;
}
