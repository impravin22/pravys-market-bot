/**
 * Stream Gemini text chunks into a live Telegram message via editMessageText.
 *
 * Port of bot/streaming.py. Design decisions:
 *  - Send a placeholder, capture its message_id, edit repeatedly.
 *  - Throttle to ~1 edit/sec/chat to stay inside Telegram's rate limits.
 *  - 4096-char Telegram cap → truncate with a footnote rather than
 *    spawning a second message.
 *  - On "can't parse entities" fall back to one plain-text edit.
 *  - Non-parse 4xx bubble up so the caller can release the rate-limit slot.
 */

import { markdownToHtml } from "./markdown_to_html";
import { TelegramClient } from "./telegram";

export const PLACEHOLDER_TEXT = "⏳ Give me a sec, mate…";
export const EDIT_INTERVAL_MS = 1200;
export const TELEGRAM_MAX_CHARS = 4096;
export const TRUNCATION_SUFFIX =
  "\n\n… (response truncated at Telegram's 4 000 character limit)";

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
   * Consume the async chunk iterator, driving edits. Returns the final
   * text that was accepted by Telegram.
   */
  async stream(chunks: AsyncIterable<string>): Promise<string> {
    await this.start();
    let buffer = "";
    for await (const piece of chunks) {
      buffer += piece;
      if (buffer.length > TELEGRAM_MAX_CHARS) {
        buffer =
          buffer.slice(0, TELEGRAM_MAX_CHARS - TRUNCATION_SUFFIX.length) + TRUNCATION_SUFFIX;
        await this.safeEdit(buffer);
        break;
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
    }
    return final;
  }
}
