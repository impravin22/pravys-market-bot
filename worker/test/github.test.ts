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

describe("dispatchWorkflow", () => {
  it("POSTs to the workflow_dispatch endpoint with ref and auth", async () => {
    const { fn, calls } = fakeFetch(new Response(null, { status: 204 }));
    await dispatchWorkflow({
      repo: "impravin22/pravys-market-bot",
      workflow: "market-pulse-morning.yml",
      token: "ghp_xxx",
      fetchImpl: fn,
    });

    expect(calls).toHaveLength(1);
    const [call] = calls;
    expect(call.url).toBe(
      "https://api.github.com/repos/impravin22/pravys-market-bot/actions/workflows/market-pulse-morning.yml/dispatches",
    );
    expect(call.init.method).toBe("POST");

    const headers = call.init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer ghp_xxx");
    expect(headers.Accept).toBe("application/vnd.github+json");
    expect(headers["X-GitHub-Api-Version"]).toBe("2022-11-28");
    expect(headers["User-Agent"]).toBe("pravys-market-bot-worker");

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
    });
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ ref: "feat/branch" });
  });

  it("throws DispatchError with status + body on non-204 responses", async () => {
    const { fn } = fakeFetch(
      new Response('{"message":"Not Found"}', {
        status: 404,
        statusText: "Not Found",
      }),
    );

    await expect(
      dispatchWorkflow({
        repo: "o/r",
        workflow: "x.yml",
        token: "t",
        fetchImpl: fn,
      }),
    ).rejects.toMatchObject({
      name: "DispatchError",
      status: 404,
    });
  });

  it("truncates large error bodies so Worker logs stay readable", async () => {
    const big = "x".repeat(2000);
    const { fn } = fakeFetch(new Response(big, { status: 500, statusText: "Err" }));
    try {
      await dispatchWorkflow({ repo: "o/r", workflow: "x.yml", token: "t", fetchImpl: fn });
      expect.unreachable("should have thrown");
    } catch (exc) {
      expect(exc).toBeInstanceOf(DispatchError);
      // 500 body chars + ellipsis ≪ original 2000 chars
      expect((exc as DispatchError).message.length).toBeLessThan(700);
      expect((exc as DispatchError).message).toContain("…");
    }
  });

  it("rejects malformed repo slugs without making a network call", async () => {
    const { fn, calls } = fakeFetch(new Response(null, { status: 204 }));
    await expect(
      dispatchWorkflow({ repo: "bad repo", workflow: "x.yml", token: "t", fetchImpl: fn }),
    ).rejects.toThrow(/invalid repo slug/);
    expect(calls).toHaveLength(0);
  });

  it("rejects workflow names that aren't .yml/.yaml", async () => {
    const { fn, calls } = fakeFetch(new Response(null, { status: 204 }));
    await expect(
      dispatchWorkflow({ repo: "o/r", workflow: "evil.sh", token: "t", fetchImpl: fn }),
    ).rejects.toThrow(/invalid workflow filename/);
    expect(calls).toHaveLength(0);
  });
});
