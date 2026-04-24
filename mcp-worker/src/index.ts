/**
 * Tiny MCP server exposing one tool: `send_telegram_message`.
 *
 * Why this exists: the Anthropic Claude RemoteTrigger sandbox blocks
 * direct egress to `api.telegram.org` (HTTP 403 host_not_allowed). The
 * trigger CAN reach our `*.workers.dev` domain, so we proxy the Bot API
 * call through this Worker. The trigger speaks MCP (Model Context
 * Protocol) over Streamable HTTP, calls our one tool, we relay to
 * Telegram, and return the message_id.
 *
 * Auth: shared bearer token in the `Authorization: Bearer <MCP_BEARER>`
 * header, stored as a Worker secret. Constant-time compared to avoid
 * leaking the secret via byte-by-byte timing differences.
 *
 * Protocol: implements the JSON-RPC 2.0 methods MCP requires for a
 * tools-only server — `initialize`, `tools/list`, `tools/call`. The
 * Anthropic connector handshakes once via `initialize`, lists tools
 * once, then calls them as the model decides.
 */

export interface Env {
  TELEGRAM_BOT_TOKEN: string;
  MCP_BEARER: string;
}

const PROTOCOL_VERSION = "2024-11-05";
const SERVER_NAME = "pravys-telegram-mcp";
const SERVER_VERSION = "0.1.0";

interface JsonRpcRequest {
  jsonrpc: "2.0";
  id?: string | number | null;
  method: string;
  params?: Record<string, unknown>;
}

interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: string | number | null;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

const SEND_TELEGRAM_TOOL = {
  name: "send_telegram_message",
  description:
    "Send a message to a Telegram chat via the Bot API. Use HTML parse mode by default — wrap headlines in <b>…</b>, italics in <i>…</i>, and escape any user-supplied < > & in the body.",
  inputSchema: {
    type: "object",
    properties: {
      chat_id: {
        description: "Telegram chat id. Group ids are negative (e.g. -5076376615), DM ids are positive.",
        type: "string",
      },
      text: {
        description: "Message body. Telegram caps individual messages at 4096 characters.",
        type: "string",
      },
      parse_mode: {
        description: "Optional Telegram parse mode. Defaults to 'HTML'.",
        type: "string",
        enum: ["HTML", "MarkdownV2", "Markdown"],
      },
      disable_web_page_preview: {
        description: "Optional. Suppresses link previews. Defaults to true for cleaner reports.",
        type: "boolean",
      },
    },
    required: ["chat_id", "text"],
    additionalProperties: false,
  },
} as const;

async function timingSafeEqual(a: string, b: string): Promise<boolean> {
  const enc = new TextEncoder();
  const da = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(a)));
  const db = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(b)));
  let diff = a.length === b.length ? 0 : 1;
  for (let i = 0; i < da.byteLength; i++) diff |= da[i]! ^ db[i]!;
  return diff === 0;
}

async function checkAuth(request: Request, env: Env): Promise<boolean> {
  const auth = request.headers.get("authorization") ?? "";
  const match = auth.match(/^Bearer\s+(.+)$/i);
  if (!match || !match[1]) return false;
  return timingSafeEqual(match[1].trim(), env.MCP_BEARER);
}

async function sendTelegram(
  env: Env,
  args: { chat_id: string; text: string; parse_mode?: string; disable_web_page_preview?: boolean },
): Promise<{ ok: true; message_id: number } | { ok: false; error: string }> {
  const body = {
    chat_id: args.chat_id,
    text: args.text,
    parse_mode: args.parse_mode ?? "HTML",
    disable_web_page_preview: args.disable_web_page_preview ?? true,
  };
  const resp = await fetch(
    `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  // Telegram surfaces semantic errors (chat not found, bad parse, message too long)
  // as HTTP 400 + JSON {"ok":false,"description":"…"}. We propagate the description
  // to the model so it can self-correct (e.g. retry without HTML mode).
  const json = (await resp.json().catch(() => null)) as
    | { ok: true; result: { message_id: number } }
    | { ok: false; description: string }
    | null;
  if (!json) return { ok: false, error: `Telegram returned non-JSON (status ${resp.status})` };
  if (json.ok) return { ok: true, message_id: json.result.message_id };
  return { ok: false, error: json.description };
}

function rpcResult(id: string | number | null, result: unknown): JsonRpcResponse {
  return { jsonrpc: "2.0", id, result };
}

function rpcError(id: string | number | null, code: number, message: string): JsonRpcResponse {
  return { jsonrpc: "2.0", id, error: { code, message } };
}

async function handleRpc(req: JsonRpcRequest, env: Env): Promise<JsonRpcResponse | null> {
  const id = req.id ?? null;
  switch (req.method) {
    case "initialize":
      return rpcResult(id, {
        protocolVersion: PROTOCOL_VERSION,
        serverInfo: { name: SERVER_NAME, version: SERVER_VERSION },
        capabilities: { tools: {} },
      });

    case "notifications/initialized":
      // MCP spec — client signals it has finished initialization.
      // No response expected for notifications.
      return null;

    case "tools/list":
      return rpcResult(id, { tools: [SEND_TELEGRAM_TOOL] });

    case "tools/call": {
      const params = (req.params ?? {}) as { name?: string; arguments?: Record<string, unknown> };
      if (params.name !== SEND_TELEGRAM_TOOL.name) {
        return rpcError(id, -32602, `Unknown tool: ${params.name}`);
      }
      const args = params.arguments ?? {};
      const chatId = args["chat_id"];
      const text = args["text"];
      if (typeof chatId !== "string" || typeof text !== "string") {
        return rpcError(id, -32602, "chat_id and text must be strings");
      }
      const result = await sendTelegram(env, {
        chat_id: chatId,
        text,
        parse_mode: typeof args["parse_mode"] === "string" ? (args["parse_mode"] as string) : undefined,
        disable_web_page_preview:
          typeof args["disable_web_page_preview"] === "boolean"
            ? (args["disable_web_page_preview"] as boolean)
            : undefined,
      });
      if (result.ok) {
        return rpcResult(id, {
          content: [
            {
              type: "text",
              text: `Sent to Telegram (message_id=${result.message_id}).`,
            },
          ],
        });
      }
      return rpcResult(id, {
        isError: true,
        content: [{ type: "text", text: `Telegram send failed: ${result.error}` }],
      });
    }

    case "ping":
      return rpcResult(id, {});

    default:
      return rpcError(id, -32601, `Method not found: ${req.method}`);
  }
}

export default {
  async fetch(request: Request, env: Env, _ctx: ExecutionContext): Promise<Response> {
    // Health check — open endpoint, no auth, no secrets revealed.
    if (request.method === "GET") {
      const url = new URL(request.url);
      if (url.pathname === "/" || url.pathname === "/health") {
        return new Response(`${SERVER_NAME} ${SERVER_VERSION} OK`, { status: 200 });
      }
    }

    if (request.method !== "POST") {
      return new Response("method not allowed", { status: 405 });
    }

    if (!(await checkAuth(request, env))) {
      // Return 401 + WWW-Authenticate so the connector knows to prompt for creds.
      return new Response("unauthorized", {
        status: 401,
        headers: { "www-authenticate": 'Bearer realm="mcp"' },
      });
    }

    let payload: JsonRpcRequest | JsonRpcRequest[];
    try {
      payload = (await request.json()) as JsonRpcRequest | JsonRpcRequest[];
    } catch {
      return new Response("bad request", { status: 400 });
    }

    // Spec allows JSON-RPC batch arrays. Handle either form.
    if (Array.isArray(payload)) {
      const responses = (await Promise.all(payload.map((p) => handleRpc(p, env)))).filter(
        (r): r is JsonRpcResponse => r !== null,
      );
      return Response.json(responses);
    }

    const response = await handleRpc(payload, env);
    if (response === null) {
      // Notification — return 202 with empty body per spec.
      return new Response(null, { status: 202 });
    }
    return Response.json(response);
  },
} satisfies ExportedHandler<Env>;
