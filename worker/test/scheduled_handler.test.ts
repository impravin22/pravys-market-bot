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

function fakeCtx(): { ctx: ExecutionContext; pending: Promise<unknown>[] } {
  const pending: Promise<unknown>[] = [];
  return {
    pending,
    ctx: {
      waitUntil: (p: Promise<unknown>) => pending.push(p),
      passThroughOnException: () => {},
    } as unknown as ExecutionContext,
  };
}

function fakeScheduled(cron: string): ScheduledController {
  return {
    cron,
    scheduledTime: Date.now(),
    noRetry: () => {},
  } as ScheduledController;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("worker.scheduled", () => {
  it("dispatches the morning pulse workflow for the morning cron", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 204 }));
    const { ctx, pending } = fakeCtx();

    await worker.scheduled!(fakeScheduled("5 23 * * *"), fakeEnv(), ctx);
    await Promise.all(pending);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/actions/workflows/market-pulse-morning.yml/dispatches");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer ghp_test");
    expect(JSON.parse(init.body as string)).toEqual({ ref: "main" });
  });

  it("maps the evening cron to the evening workflow", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 204 }));
    const { ctx, pending } = fakeCtx();

    await worker.scheduled!(fakeScheduled("15 10 * * 2-6"), fakeEnv(), ctx);
    await Promise.all(pending);

    const [url] = fetchSpy.mock.calls[0] as [string];
    expect(url).toContain("/actions/workflows/market-pulse-evening.yml/dispatches");
  });

  it("maps the weekly cron to the weekly workflow", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 204 }));
    const { ctx, pending } = fakeCtx();

    await worker.scheduled!(fakeScheduled("0 14 * * 1"), fakeEnv(), ctx);
    await Promise.all(pending);

    const [url] = fetchSpy.mock.calls[0] as [string];
    expect(url).toContain("/actions/workflows/weekly-top3.yml/dispatches");
  });

  it("throws (so CF marks the run failed) when the cron is not in the mapping", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { ctx, pending } = fakeCtx();

    await expect(
      worker.scheduled!(fakeScheduled("0 0 * * *"), fakeEnv(), ctx),
    ).rejects.toThrow(/no workflow mapped for cron "0 0 \* \* \*"/);

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(pending).toHaveLength(0);
  });

  it("propagates dispatch errors so Cloudflare marks the run failed", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response('{"message":"Bad credentials"}', { status: 401, statusText: "Unauthorized" }),
    );
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { ctx, pending } = fakeCtx();

    await worker.scheduled!(fakeScheduled("5 23 * * *"), fakeEnv(), ctx);
    await expect(Promise.all(pending)).rejects.toThrow(/dispatch failed: 401/);
  });

  it("uses env.GITHUB_REF when set to a non-main branch", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 204 }));
    const { ctx, pending } = fakeCtx();

    await worker.scheduled!(
      fakeScheduled("5 23 * * *"),
      fakeEnv({ GITHUB_REF: "feat/branch" }),
      ctx,
    );
    await Promise.all(pending);

    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ ref: "feat/branch" });
  });
});
