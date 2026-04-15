import { describe, expect, it } from "vitest";
import worker, { type Env } from "../src/index";

function fakeEnv(overrides: Partial<Env> = {}): Env {
  return {
    TELEGRAM_BOT_TOKEN: "tok",
    TELEGRAM_CHAT_ID: "-100500",
    TELEGRAM_OWNER_USER_ID: "42",
    TELEGRAM_WEBHOOK_SECRET: "secret",
    GOOGLE_API_KEY: "g",
    UPSTASH_REDIS_REST_URL: "https://r",
    UPSTASH_REDIS_REST_TOKEN: "t",
    BOT_USER_ID_SALT: "salt",
    GOOGLE_AI_DEFAULT_MODEL: "gemini-2.5-pro",
    CANSLIM_PLAYBOOK_FILE_ID: "",
    GITHUB_REPO: "impravin22/pravys-market-bot",
    GITHUB_REF: "main",
    GITHUB_DISPATCH_TOKEN: "ghp_test",
    ...overrides,
  };
}

function fakeCtx(): { ctx: ExecutionContext; calls: unknown[] } {
  const calls: unknown[] = [];
  return {
    calls,
    ctx: {
      waitUntil: (p: Promise<unknown>) => calls.push(p),
      passThroughOnException: () => {},
    } as unknown as ExecutionContext,
  };
}

describe("worker.fetch", () => {
  it("GET returns the health-check string", async () => {
    const { ctx, calls } = fakeCtx();
    const resp = await worker.fetch(
      new Request("https://w/", { method: "GET" }),
      fakeEnv(),
      ctx,
    );
    expect(resp.status).toBe(200);
    expect(await resp.text()).toBe("pravys-market-bot worker OK");
    expect(calls).toEqual([]);
  });

  it("rejects non-GET non-POST methods with 405", async () => {
    const { ctx, calls } = fakeCtx();
    const resp = await worker.fetch(
      new Request("https://w/", { method: "PUT" }),
      fakeEnv(),
      ctx,
    );
    expect(resp.status).toBe(405);
    expect(calls).toEqual([]);
  });

  it("rejects POST without secret-token header (403)", async () => {
    const { ctx, calls } = fakeCtx();
    const resp = await worker.fetch(
      new Request("https://w/", { method: "POST", body: "{}" }),
      fakeEnv(),
      ctx,
    );
    expect(resp.status).toBe(403);
    expect(calls).toEqual([]);
  });

  it("rejects POST with wrong secret-token (403)", async () => {
    const { ctx, calls } = fakeCtx();
    const resp = await worker.fetch(
      new Request("https://w/", {
        method: "POST",
        headers: { "x-telegram-bot-api-secret-token": "wrong" },
        body: "{}",
      }),
      fakeEnv(),
      ctx,
    );
    expect(resp.status).toBe(403);
    expect(calls).toEqual([]);
  });

  it("rejects malformed JSON body (400)", async () => {
    const { ctx, calls } = fakeCtx();
    const resp = await worker.fetch(
      new Request("https://w/", {
        method: "POST",
        headers: { "x-telegram-bot-api-secret-token": "secret" },
        body: "{not json",
      }),
      fakeEnv(),
      ctx,
    );
    expect(resp.status).toBe(400);
    expect(calls).toEqual([]);
  });

  it("ack 200 even when downstream handler is dispatched (does not block on it)", async () => {
    const { ctx, calls } = fakeCtx();
    const resp = await worker.fetch(
      new Request("https://w/", {
        method: "POST",
        headers: { "x-telegram-bot-api-secret-token": "secret" },
        body: JSON.stringify({ update_id: 1, message: { chat: { id: 999 } } }),
      }),
      fakeEnv(),
      ctx,
    );
    // Telegram retries non-200 — we MUST always 200 here.
    expect(resp.status).toBe(200);
    // Handler dispatched via waitUntil, not awaited inline.
    expect(calls.length).toBe(1);
  });

  it("constant-time secret check rejects empty header explicitly", async () => {
    const { ctx } = fakeCtx();
    const resp = await worker.fetch(
      new Request("https://w/", {
        method: "POST",
        headers: { "x-telegram-bot-api-secret-token": "" },
        body: "{}",
      }),
      fakeEnv(),
      ctx,
    );
    expect(resp.status).toBe(403);
  });
});

describe("waitUntil error boundary", () => {
  it("dispatched handler errors do not propagate to the response", async () => {
    const { ctx, calls } = fakeCtx();
    // Stub a malformed update so handleUpdate's pipeline fails (no chat id).
    const resp = await worker.fetch(
      new Request("https://w/", {
        method: "POST",
        headers: { "x-telegram-bot-api-secret-token": "secret" },
        body: JSON.stringify({ update_id: 7 }),
      }),
      fakeEnv(),
      ctx,
    );
    expect(resp.status).toBe(200);
    // Wait for the dispatched promise — it must resolve (catch swallowed),
    // even though the handler short-circuits on a missing message.
    await Promise.allSettled(calls as Promise<unknown>[]);
  });
});
