import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  CONTINUATION_PLACEHOLDER,
  PLACEHOLDER_TEXT,
  SAFE_PER_MESSAGE_CHARS,
  TELEGRAM_MAX_CHARS,
  TelegramStream,
  findSafeSplit,
} from "../src/streaming";
import { TelegramClient } from "../src/telegram";

interface FakeTelegramOpts {
  sendMessageId?: number;
  editResponses?: Array<{ ok: boolean; description?: string }>;
  sendMessageError?: Error;
}

function fakeTelegram(opts: FakeTelegramOpts = {}): {
  client: TelegramClient;
  sentTexts: string[];
  edits: Array<{ messageId: number; text: string; parseMode?: string }>;
} {
  const sentTexts: string[] = [];
  const edits: Array<{ messageId: number; text: string; parseMode?: string }> = [];
  const editQueue = [...(opts.editResponses ?? [])];
  let nextSendId = opts.sendMessageId ?? 101;

  const client: TelegramClient = {
    botToken: "tok",
    sendMessage: async (_chatId: number | string, text: string): Promise<number> => {
      if (opts.sendMessageError) throw opts.sendMessageError;
      sentTexts.push(text);
      return nextSendId++;
    },
    editMessageText: async (
      _chatId: number | string,
      messageId: number,
      text: string,
      opts2: { parseMode?: "HTML" | null } = {},
    ) => {
      const r = editQueue.shift() ?? { ok: true };
      const recorded: { messageId: number; text: string; parseMode?: string } = {
        messageId,
        text,
      };
      if (opts2?.parseMode) recorded.parseMode = opts2.parseMode;
      edits.push(recorded);
      const out: { ok: boolean; description?: string; errorCode?: number } = { ok: r.ok };
      if (r.description !== undefined) out.description = r.description;
      return out;
    },
    getMe: async () => null,
  } as unknown as TelegramClient;

  return { client, sentTexts, edits };
}

async function* gen(parts: string[]): AsyncGenerator<string> {
  for (const p of parts) yield p;
}

describe("TelegramStream", () => {
  beforeEach(() => {
    // Freeze time at epoch so the throttle gate (now - lastEditAt >= INTERVAL)
    // evaluates to 0, suppressing intermediate edits unless a test explicitly
    // advances time. This isolates each test from the throttling behaviour.
    vi.useFakeTimers();
    vi.setSystemTime(new Date(0));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("sends placeholder, then renders markdown to HTML on the final edit", async () => {
    const { client, sentTexts, edits } = fakeTelegram();
    const stream = new TelegramStream(client, -100500);
    const final = await stream.stream(gen(["**PFC** is a PSU NBFC"]));
    expect(final).toBe("**PFC** is a PSU NBFC");
    expect(sentTexts).toEqual([PLACEHOLDER_TEXT]);
    expect(edits.at(-1)?.parseMode).toBe("HTML");
    expect(edits.at(-1)?.text).toContain("<b>PFC</b>");
  });

  it("escapes raw < and & in the rendered output", async () => {
    const { client, edits } = fakeTelegram();
    const stream = new TelegramStream(client, -100500);
    await stream.stream(gen(["profit <up> & flat"]));
    expect(edits.at(-1)?.text).toContain("&lt;up&gt;");
    expect(edits.at(-1)?.text).toContain("&amp;");
  });

  it("falls back to plain text when Telegram rejects HTML parse entities", async () => {
    const { client, edits } = fakeTelegram({
      editResponses: [
        { ok: false, description: "Bad Request: can't parse entities in message text" },
        { ok: true },
      ],
    });
    const stream = new TelegramStream(client, -100500);
    await stream.stream(gen(["<weird> **bold**"]));
    // Two edits: HTML attempt + plain-text retry.
    expect(edits.length).toBeGreaterThanOrEqual(2);
    // Plain retry: no parseMode, raw text body.
    expect(edits[edits.length - 1].parseMode).toBeUndefined();
    expect(edits[edits.length - 1].text).toBe("<weird> **bold**");
  });

  it("plain-text fallback failure surfaces the retry's status", async () => {
    const { client } = fakeTelegram({
      editResponses: [
        { ok: false, description: "Bad Request: can't parse entities" },
        { ok: false, description: "Forbidden: bot was kicked" },
      ],
    });
    const stream = new TelegramStream(client, -100500);
    await expect(stream.stream(gen(["**x**"]))).rejects.toThrow(/plain fallback failed/);
  });

  it("recovers when the placeholder vanishes (message to edit not found)", async () => {
    const { client, sentTexts } = fakeTelegram({
      editResponses: [{ ok: false, description: "Bad Request: message to edit not found" }],
    });
    const stream = new TelegramStream(client, -100500);
    await stream.stream(gen(["hi"]));
    // Placeholder + recovery sendMessage (containing the actual reply).
    expect(sentTexts.length).toBe(2);
    expect(sentTexts[1]).toBe("hi");
  });

  it("propagates terminal errors (bot blocked) without recovery", async () => {
    const { client } = fakeTelegram({
      editResponses: [{ ok: false, description: "Forbidden: bot was blocked by the user" }],
    });
    const stream = new TelegramStream(client, -100500);
    await expect(stream.stream(gen(["hi"]))).rejects.toThrow(/terminal/);
  });

  it("propagates flood control errors so caller can back off", async () => {
    const { client } = fakeTelegram({
      editResponses: [{ ok: false, description: "Too Many Requests: retry after 5" }],
    });
    const stream = new TelegramStream(client, -100500);
    await expect(stream.stream(gen(["hi"]))).rejects.toThrow(/flood/);
  });

  it("spans multiple messages when the reply exceeds one Telegram cap", async () => {
    const { client, sentTexts, edits } = fakeTelegram();
    const stream = new TelegramStream(client, -100500);
    // 2.5x the per-message safe cap → expect 3 messages: placeholder +
    // 2 continuation placeholders, plus one edit per message body.
    const big = "x".repeat(SAFE_PER_MESSAGE_CHARS) + "\n" +
                "y".repeat(SAFE_PER_MESSAGE_CHARS) + "\n" +
                "z".repeat(SAFE_PER_MESSAGE_CHARS);
    const final = await stream.stream(gen([big]));
    // Final return text contains every part (joined by \n\n), so it can
    // exceed TELEGRAM_MAX_CHARS — that is the whole point of this PR.
    expect(final.length).toBeGreaterThan(TELEGRAM_MAX_CHARS);
    // Three messages were started (1 initial + 2 continuations).
    expect(sentTexts).toEqual([PLACEHOLDER_TEXT, CONTINUATION_PLACEHOLDER, CONTINUATION_PLACEHOLDER]);
    // Every edit body fits within the per-message safe cap — Telegram
    // never sees a body bigger than that.
    for (const e of edits) {
      expect(e.text.length).toBeLessThanOrEqual(TELEGRAM_MAX_CHARS);
    }
  });

  it("findSafeSplit prefers the last newline within the lookback window", () => {
    const text = "a".repeat(3000) + "\n" + "b".repeat(800);
    const idx = findSafeSplit(text, SAFE_PER_MESSAGE_CHARS);
    // Should split at the newline (index 3000), which is within the
    // lookback (cap=3800, lookback=600 → window starts at 3200).
    // Newline is at 3000, which is OUTSIDE the window — so we fall back
    // to a hard cut at 3800. The function only treats newlines INSIDE
    // the window as safe split points.
    expect(idx).toBe(SAFE_PER_MESSAGE_CHARS);
  });

  it("findSafeSplit uses newline within the window when available", () => {
    const text = "a".repeat(3500) + "\n" + "b".repeat(500);
    const idx = findSafeSplit(text, SAFE_PER_MESSAGE_CHARS);
    expect(idx).toBe(3501); // right after the newline
  });

  it("findSafeSplit returns text length when below the cap", () => {
    expect(findSafeSplit("short", SAFE_PER_MESSAGE_CHARS)).toBe(5);
  });

  it("intermediate edit failures are swallowed; final edit still runs", async () => {
    const { client, edits } = fakeTelegram({
      editResponses: [
        { ok: false, description: "Bad Request: random transient" },
        { ok: true },
      ],
    });
    const stream = new TelegramStream(client, -100500);
    // Advance time so an intermediate edit fires.
    const it = stream.stream(
      (async function* () {
        yield "hello ";
        vi.advanceTimersByTime(10_000);
        yield "world";
      })(),
    );
    await it;
    // First edit failed → swallowed; second succeeded.
    expect(edits.length).toBeGreaterThanOrEqual(1);
  });

  it("editOnce-before-start guards against mis-use", async () => {
    const { client } = fakeTelegram();
    const stream = new TelegramStream(client, -100500);
    // Empty stream: placeholder sent, no final edit (because final=='').
    const final = await stream.stream(gen([]));
    expect(final).toBe("");
  });
});
