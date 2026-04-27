import { describe, expect, it, vi } from "vitest";
import { fetchHistory, latestClose } from "../src/yahoo";

function makeResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SAMPLE = {
  chart: {
    result: [
      {
        timestamp: [1714080000, 1714166400, 1714252800],
        indicators: {
          quote: [
            {
              open: [100, 101, 102],
              high: [103, 104, 105],
              low: [99, 100, 101],
              close: [102, 103, 104],
              volume: [1_000_000, 1_200_000, 1_100_000],
            },
          ],
        },
      },
    ],
  },
};

describe("fetchHistory", () => {
  it("parses Yahoo chart payload into bars", async () => {
    const fetchImpl = vi.fn().mockResolvedValueOnce(makeResponse(SAMPLE));
    const history = await fetchHistory("RELIANCE.NS", { fetchImpl: fetchImpl as unknown as typeof fetch });
    expect(history).not.toBeNull();
    expect(history?.bars).toHaveLength(3);
    expect(history?.bars[2].close).toBe(104);
    expect(history?.bars[2].volume).toBe(1_100_000);
  });

  it("returns null on non-200", async () => {
    const fetchImpl = vi.fn().mockResolvedValueOnce(new Response("err", { status: 500 }));
    expect(
      await fetchHistory("X", { fetchImpl: fetchImpl as unknown as typeof fetch }),
    ).toBeNull();
  });

  it("returns null on transport failure", async () => {
    const fetchImpl = vi.fn().mockRejectedValueOnce(new Error("dns"));
    expect(
      await fetchHistory("X", { fetchImpl: fetchImpl as unknown as typeof fetch }),
    ).toBeNull();
  });

  it("returns null when no result entries", async () => {
    const fetchImpl = vi.fn().mockResolvedValueOnce(makeResponse({ chart: { result: [] } }));
    expect(
      await fetchHistory("X", { fetchImpl: fetchImpl as unknown as typeof fetch }),
    ).toBeNull();
  });

  it("uses self-identifying User-Agent", async () => {
    const fetchImpl = vi.fn().mockResolvedValueOnce(makeResponse(SAMPLE));
    await fetchHistory("X", { fetchImpl: fetchImpl as unknown as typeof fetch });
    const init = fetchImpl.mock.calls[0][1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers["User-Agent"].toLowerCase()).toContain("pravys-market-bot");
  });
});

describe("latestClose", () => {
  it("returns the last non-null close", () => {
    const c = latestClose({
      symbol: "X",
      bars: [
        { timestamp: 1, open: null, high: null, low: null, close: 100, volume: null },
        { timestamp: 2, open: null, high: null, low: null, close: 101, volume: null },
        { timestamp: 3, open: null, high: null, low: null, close: null, volume: null },
      ],
    });
    expect(c).toBe(101);
  });

  it("returns null when no usable closes", () => {
    expect(
      latestClose({
        symbol: "X",
        bars: [{ timestamp: 1, open: null, high: null, low: null, close: null, volume: null }],
      }),
    ).toBeNull();
  });
});
