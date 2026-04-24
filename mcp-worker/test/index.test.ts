/**
 * Unit tests for the Telegram MCP worker. Network calls to the Bot API
 * are mocked via the global `fetch` — we only verify our JSON-RPC
 * plumbing and the auth gate.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import worker, { type Env } from "../src/index";

const BEARER = "test-bearer-token-do-not-commit";
const BOT_TOKEN = "0000:fake";

const env: Env = {
  TELEGRAM_BOT_TOKEN: BOT_TOKEN,
  MCP_BEARER: BEARER,
};

function rpc(method: string, params?: Record<string, unknown>, id: number | string | null = 1) {
  return { jsonrpc: "2.0" as const, id, method, params };
}

async function call(body: unknown, headers: Record<string, string> = {}): Promise<Response> {
  return worker.fetch(
    new Request("https://mcp.example/", {
      method: "POST",
      headers: { "content-type": "application/json", ...headers },
      body: JSON.stringify(body),
    }),
    env,
    {} as ExecutionContext,
  );
}

describe("auth gate", () => {
  it("rejects missing bearer", async () => {
    const resp = await call(rpc("tools/list"));
    expect(resp.status).toBe(401);
    expect(resp.headers.get("www-authenticate")).toContain("Bearer");
  });

  it("rejects wrong bearer", async () => {
    const resp = await call(rpc("tools/list"), { authorization: "Bearer wrong" });
    expect(resp.status).toBe(401);
  });

  it("accepts correct bearer", async () => {
    const resp = await call(rpc("tools/list"), { authorization: `Bearer ${BEARER}` });
    expect(resp.status).toBe(200);
  });
});

describe("health check", () => {
  it("returns OK on GET /", async () => {
    const resp = await worker.fetch(
      new Request("https://mcp.example/", { method: "GET" }),
      env,
      {} as ExecutionContext,
    );
    expect(resp.status).toBe(200);
    expect(await resp.text()).toContain("pravys-telegram-mcp");
  });
});

describe("MCP protocol", () => {
  it("initialize returns protocol version and tool capability", async () => {
    const resp = await call(rpc("initialize"), { authorization: `Bearer ${BEARER}` });
    const body = (await resp.json()) as { result: { protocolVersion: string; capabilities: unknown } };
    expect(body.result.protocolVersion).toBe("2024-11-05");
    expect(body.result.capabilities).toHaveProperty("tools");
  });

  it("tools/list advertises send_telegram_message", async () => {
    const resp = await call(rpc("tools/list"), { authorization: `Bearer ${BEARER}` });
    const body = (await resp.json()) as { result: { tools: Array<{ name: string }> } };
    expect(body.result.tools).toHaveLength(1);
    expect(body.result.tools[0]?.name).toBe("send_telegram_message");
  });

  it("notifications/initialized returns 202 with no body", async () => {
    const resp = await call(
      { jsonrpc: "2.0" as const, method: "notifications/initialized" },
      { authorization: `Bearer ${BEARER}` },
    );
    expect(resp.status).toBe(202);
  });

  it("unknown method returns -32601", async () => {
    const resp = await call(rpc("bogus/method"), { authorization: `Bearer ${BEARER}` });
    const body = (await resp.json()) as { error: { code: number } };
    expect(body.error.code).toBe(-32601);
  });
});

describe("tools/call send_telegram_message", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    // Mock the Telegram Bot API response path.
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      if (url.includes("api.telegram.org")) {
        const body = JSON.parse(init?.body as string) as {
          chat_id: string;
          text: string;
          parse_mode: string;
        };
        // Simulate bad parse error when parse_mode is HTML and text contains a bare <.
        if (body.parse_mode === "HTML" && body.text.includes("<raw>")) {
          return new Response(
            JSON.stringify({ ok: false, description: "Bad Request: can't parse entities" }),
            { status: 400, headers: { "content-type": "application/json" } },
          );
        }
        return new Response(
          JSON.stringify({ ok: true, result: { message_id: 12345 } }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("unexpected url", { status: 500 });
    }) as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("happy path returns message_id in content", async () => {
    const resp = await call(
      rpc("tools/call", {
        name: "send_telegram_message",
        arguments: { chat_id: "-5076376615", text: "<b>hi</b>" },
      }),
      { authorization: `Bearer ${BEARER}` },
    );
    const body = (await resp.json()) as {
      result: { content: Array<{ type: string; text: string }> };
    };
    expect(body.result.content[0]?.text).toContain("message_id=12345");
  });

  it("telegram error surfaces isError with description", async () => {
    const resp = await call(
      rpc("tools/call", {
        name: "send_telegram_message",
        arguments: { chat_id: "-5076376615", text: "<raw>" },
      }),
      { authorization: `Bearer ${BEARER}` },
    );
    const body = (await resp.json()) as {
      result: { isError?: boolean; content: Array<{ text: string }> };
    };
    expect(body.result.isError).toBe(true);
    expect(body.result.content[0]?.text).toContain("can't parse entities");
  });

  it("missing chat_id rejects with -32602", async () => {
    const resp = await call(
      rpc("tools/call", {
        name: "send_telegram_message",
        arguments: { text: "orphan" },
      }),
      { authorization: `Bearer ${BEARER}` },
    );
    const body = (await resp.json()) as { error: { code: number } };
    expect(body.error.code).toBe(-32602);
  });

  it("unknown tool name rejects with -32602", async () => {
    const resp = await call(
      rpc("tools/call", { name: "nuke_earth", arguments: {} }),
      { authorization: `Bearer ${BEARER}` },
    );
    const body = (await resp.json()) as { error: { code: number } };
    expect(body.error.code).toBe(-32602);
  });
});
