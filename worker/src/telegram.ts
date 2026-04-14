/**
 * Minimal Telegram Bot API client for the Worker.
 *
 * The Worker receives webhooks (not poll-based), so we only need the
 * outbound send/edit primitives. No long-polling offset to manage.
 */

export interface TelegramMessage {
  text?: string;
  chat: { id: number; type: string };
  from?: { id: number; is_bot?: boolean; username?: string };
  entities?: Array<{ type: string; offset: number; length: number }>;
  photo?: unknown;
  document?: unknown;
}

export interface TelegramUpdate {
  update_id: number;
  message?: TelegramMessage;
  edited_message?: TelegramMessage;
  channel_post?: TelegramMessage;
}

export class TelegramClient {
  private readonly base: string;

  constructor(
    public readonly botToken: string,
    private readonly fetchImpl: typeof fetch = fetch,
  ) {
    this.base = `https://api.telegram.org/bot${botToken}`;
  }

  async sendMessage(
    chatId: number | string,
    text: string,
    opts: { parseMode?: "HTML" | null } = {},
  ): Promise<number> {
    const body: Record<string, string> = {
      chat_id: String(chatId),
      text,
      disable_web_page_preview: "true",
    };
    if (opts.parseMode) body.parse_mode = opts.parseMode;
    const resp = await this.fetchImpl(`${this.base}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await resp.json() as { ok: boolean; result?: { message_id: number }; description?: string };
    if (!payload.ok || !payload.result) {
      throw new Error(`sendMessage failed status=${resp.status}: ${payload.description ?? "unknown"}`);
    }
    return payload.result.message_id;
  }

  async editMessageText(
    chatId: number | string,
    messageId: number,
    text: string,
    opts: { parseMode?: "HTML" | null } = {},
  ): Promise<{ ok: boolean; description?: string; errorCode?: number }> {
    const body: Record<string, string> = {
      chat_id: String(chatId),
      message_id: String(messageId),
      text,
      disable_web_page_preview: "true",
    };
    if (opts.parseMode) body.parse_mode = opts.parseMode;
    const resp = await this.fetchImpl(`${this.base}/editMessageText`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await resp.json() as { ok: boolean; description?: string };
    const out: { ok: boolean; description?: string; errorCode?: number } = {
      ok: payload.ok,
      errorCode: resp.status,
    };
    if (payload.description !== undefined) out.description = payload.description;
    return out;
  }

  async getMe(): Promise<{ username?: string } | null> {
    const resp = await this.fetchImpl(`${this.base}/getMe`);
    const payload = await resp.json() as { ok: boolean; result?: { username?: string } };
    if (!payload.ok || !payload.result) return null;
    return payload.result;
  }
}
