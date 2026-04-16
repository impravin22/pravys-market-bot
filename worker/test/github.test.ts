import { describe, expect, it, vi } from "vitest";
import { DispatchError, dispatchWorkflow } from "../src/github";

interface FetchCall {
  url: string;
  init: RequestInit;
}

function fakeFetch(response: Response): { fn: typeof fetch; calls: FetchCall[] } {
  const calls: FetchCall[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init: init ?? {} });
    return response;
  }) as unknown as typeof fetch;
  return { fn, calls };
}

function headerValue(init: RequestInit, name: string): string | null {
  // Go through Headers (rather than casting to Record<string,string>) so the
  // assertion survives if the implementation ever passes a Headers object.
  return new Headers(init.headers).get(name);
}

describe("dispatchWorkflow", () => {
  it("POSTs to the workflow_dispatch endpoint with ref and auth", async () => {
    const { fn, calls } = fakeFetch(new Response(null, { status: 204 }));
    await dispatchWorkflow({
      repo: "impravin22/pravys-market-bot",
      workflow: "market-pulse-morning.yml",
      token: "ghp_xxx",
      fetchImpl: fn,
      timeoutMs: 0,
    });

    expect(calls).toHaveLength(1);
    const [call] = calls;
    expect(call.url).toBe(
      "https://api.github.com/repos/impravin22/pravys-market-bot/actions/workflows/market-pulse-morning.yml/dispatches",
    );
    expect(call.init.method).toBe("POST");

    expect(headerValue(call.init, "Authorization")).toBe("Bearer ghp_xxx");
    expect(headerValue(call.init, "Accept")).toBe("application/vnd.github+json");
    expect(headerValue(call.init, "X-GitHub-Api-Version")).toBe("2022-11-28");
    expect(headerValue(call.init, "User-Agent")).toBe("pravys-market-bot-worker");

    expect(JSON.parse(call.init.body as string)).toEqual({ ref: "main" });
  });

  it("uses a caller-supplied ref when provided", async () => {
    const { fn, calls } = fakeFetch(new Response(null, { status: 204 }));
    await dispatchWorkflow({
      repo: "o/r",
      workflow: "x.yml",
      ref: "feat/branch",
      token: "t",
      fetchImpl: fn,
      timeoutMs: 0,
    });
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ ref: "feat/branch" });
  });

  it.each([
    { status: 401, statusText: "Unauthorized", body: '{"message":"Bad credentials"}' },
    { status: 404, statusText: "Not Found", body: '{"message":"Not Found"}' },
    { status: 422, statusText: "Unprocessable Entity", body: '{"message":"No ref found"}' },
    { status: 500, statusText: "Server Error", body: "oops" },
  ])("throws DispatchError with status $status for GitHub error responses", async ({ status, statusText, body }) => {
    const { fn } = fakeFetch(new Response(body, { status, statusText }));
    await expect(
      dispatchWorkflow({ repo: "o/r", workflow: "x.yml", token: "t", fetchImpl: fn, timeoutMs: 0 }),
    ).rejects.toMatchObject({
      name: "DispatchError",
      status,
    });
  });

  it("truncates large error bodies so Worker logs stay readable", async () => {
    const big = "x".repeat(2000);
    const { fn } = fakeFetch(new Response(big, { status: 500, statusText: "Err" }));
    try {
      await dispatchWorkflow({ repo: "o/r", workflow: "x.yml", token: "t", fetchImpl: fn, timeoutMs: 0 });
      expect.unreachable("should have thrown");
    } catch (exc) {
      expect(exc).toBeInstanceOf(DispatchError);
      expect((exc as DispatchError).message.length).toBeLessThan(700);
      expect((exc as DispatchError).message).toContain("…");
    }
  });

  it("throws DispatchError(status=0) when the token is empty — no network call", async () => {
    const { fn, calls } = fakeFetch(new Response(null, { status: 204 }));
    await expect(
      dispatchWorkflow({ repo: "o/r", workflow: "x.yml", token: "", fetchImpl: fn, timeoutMs: 0 }),
    ).rejects.toMatchObject({
      name: "DispatchError",
      status: 0,
      message: expect.stringMatching(/empty or unset/i),
    });
    expect(calls).toHaveLength(0);
  });

  it("rejects malformed repo slugs without making a network call", async () => {
    const { fn, calls } = fakeFetch(new Response(null, { status: 204 }));
    await expect(
      dispatchWorkflow({ repo: "bad repo", workflow: "x.yml", token: "t", fetchImpl: fn, timeoutMs: 0 }),
    ).rejects.toThrow(/invalid repo slug/);
    expect(calls).toHaveLength(0);
  });

  it("rejects workflow names that aren't .yml/.yaml", async () => {
    const { fn, calls } = fakeFetch(new Response(null, { status: 204 }));
    await expect(
      dispatchWorkflow({ repo: "o/r", workflow: "evil.sh", token: "t", fetchImpl: fn, timeoutMs: 0 }),
    ).rejects.toThrow(/invalid workflow filename/);
    expect(calls).toHaveLength(0);
  });

  it("wraps fetch network errors as DispatchError(status=0)", async () => {
    const fn: typeof fetch = vi.fn(async () => {
      throw new TypeError("network down");
    }) as unknown as typeof fetch;
    await expect(
      dispatchWorkflow({ repo: "o/r", workflow: "x.yml", token: "t", fetchImpl: fn, timeoutMs: 0 }),
    ).rejects.toMatchObject({
      name: "DispatchError",
      status: 0,
      message: expect.stringContaining("network down"),
    });
  });

  it("returns '<unreadable body>' when the response body read fails", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const resp = new Response("not inspected", { status: 500, statusText: "Err" });
    // Replace text() with a rejecting stub so safeReadText's catch path fires.
    Object.defineProperty(resp, "text", {
      value: () => Promise.reject(new Error("stream aborted")),
    });
    const fn: typeof fetch = vi.fn(async () => resp) as unknown as typeof fetch;

    await expect(
      dispatchWorkflow({ repo: "o/r", workflow: "x.yml", token: "t", fetchImpl: fn, timeoutMs: 0 }),
    ).rejects.toMatchObject({
      name: "DispatchError",
      status: 500,
      message: expect.stringContaining("<unreadable body>"),
    });
  });
});
