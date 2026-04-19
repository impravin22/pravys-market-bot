import { afterEach, describe, expect, it, vi } from "vitest";
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

function fakeCtx(): ExecutionContext {
  return {
    waitUntil: () => {},
    passThroughOnException: () => {},
  } as unknown as ExecutionContext;
}

function fakeScheduled(cron: string): ScheduledController {
  return {
    cron,
    scheduledTime: Date.now(),
    noRetry: () => {},
  } as ScheduledController;
}

function headerValue(init: RequestInit, name: string): string | null {
  return new Headers(init.headers).get(name);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("worker.scheduled", () => {
  it("dispatches the morning pulse workflow for the morning cron", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 204 }));
    const infoSpy = vi.spyOn(console, "info").mockImplementation(() => {});

    await worker.scheduled!(fakeScheduled("0 3 * * 2-6"), fakeEnv(), fakeCtx());

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/actions/workflows/market-pulse-morning.yml/dispatches");
    expect(headerValue(init, "Authorization")).toBe("Bearer ghp_test");
    expect(JSON.parse(init.body as string)).toEqual({ ref: "main" });
    expect(infoSpy).toHaveBeenCalledWith(
      "scheduled: dispatched",
      "market-pulse-morning.yml",
      "for cron",
      "0 3 * * 2-6",
    );
  });

  it("maps the Sat weekly-recap cron to the weekly-recap workflow", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.spyOn(console, "info").mockImplementation(() => {});

    await worker.scheduled!(fakeScheduled("0 14 * * 7"), fakeEnv(), fakeCtx());

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url] = fetchSpy.mock.calls[0] as [string];
    expect(url).toContain("/actions/workflows/weekly-recap.yml/dispatches");
  });

  it("maps the Sun weekly cron to the weekly-top3 workflow", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.spyOn(console, "info").mockImplementation(() => {});

    await worker.scheduled!(fakeScheduled("0 2 * * *"), fakeEnv(), fakeCtx());

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url] = fetchSpy.mock.calls[0] as [string];
    expect(url).toContain("/actions/workflows/weekly-top3.yml/dispatches");
  });

  it("throws (so CF marks the run failed) when the cron is not in the mapping", async () => {
    // The Telegram canary still fires before throw — assert it went to Telegram
    // (not GitHub) so a missing mapping never silently dispatches.
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response('{"ok":true,"result":{"message_id":1}}', { status: 200 }),
      );

    await expect(
      worker.scheduled!(fakeScheduled("0 0 * * *"), fakeEnv(), fakeCtx()),
    ).rejects.toThrow(/no workflow mapped for cron "0 0 \* \* \*"/);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(String(fetchSpy.mock.calls[0][0])).toContain("api.telegram.org");
  });

  it.each([
    { status: 401, label: "invalid token" },
    { status: 404, label: "wrong workflow or repo" },
    { status: 422, label: "ref does not exist" },
  ])("propagates a $status ($label) so Cloudflare marks the run failed", async ({ status }) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response('{"message":"error"}', { status, statusText: "Error" }),
    );
    vi.spyOn(console, "error").mockImplementation(() => {});

    await expect(
      worker.scheduled!(fakeScheduled("0 3 * * 2-6"), fakeEnv(), fakeCtx()),
    ).rejects.toThrow(new RegExp(`dispatch failed: ${status}`));
  });

  it("propagates an empty-token error as a failed scheduled run", async () => {
    // dispatchWorkflow throws on the empty-token guard before fetching
    // GitHub, but the canary still pings Telegram so the operator sees
    // exactly which env var is empty in prod.
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response('{"ok":true,"result":{"message_id":1}}', { status: 200 }),
      );
    vi.spyOn(console, "error").mockImplementation(() => {});

    await expect(
      worker.scheduled!(
        fakeScheduled("0 3 * * 2-6"),
        fakeEnv({ GITHUB_DISPATCH_TOKEN: "" }),
        fakeCtx(),
      ),
    ).rejects.toThrow(/empty or unset/i);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(String(fetchSpy.mock.calls[0][0])).toContain("api.telegram.org");
    // The canary message body must include the env snapshot so the failure
    // mode is diagnosable without a wrangler tail session.
    const init = fetchSpy.mock.calls[0][1] as RequestInit;
    const body = JSON.parse(init.body as string) as { text: string };
    expect(body.text).toMatch(/token_len=0/);
    expect(body.text).toMatch(/error: DispatchError/);
  });

  it("uses env.GITHUB_REF when set to a non-main branch", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.spyOn(console, "info").mockImplementation(() => {});

    await worker.scheduled!(
      fakeScheduled("0 3 * * 2-6"),
      fakeEnv({ GITHUB_REF: "feat/branch" }),
      fakeCtx(),
    );

    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ ref: "feat/branch" });
  });

  it("logs the error context before throwing so the Worker tail has a hint", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response('{"message":"Bad credentials"}', { status: 401, statusText: "Unauthorized" }),
    );
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    await expect(
      worker.scheduled!(fakeScheduled("0 3 * * 2-6"), fakeEnv(), fakeCtx()),
    ).rejects.toThrow(/401/);

    expect(errorSpy).toHaveBeenCalledWith(
      "scheduled: dispatch failed",
      "market-pulse-morning.yml",
      expect.any(Error),
    );
  });
});
