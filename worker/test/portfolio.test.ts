import { describe, expect, it, vi } from "vitest";
import { PortfolioStore, makeHolding, readPicksCache } from "../src/portfolio";
import { RedisStore } from "../src/redis_store";

type FetchMock = ReturnType<typeof vi.fn>;

function makeResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeStore(fetchMock: FetchMock): RedisStore {
  return new RedisStore({
    url: "https://mock.upstash",
    token: "secret",
    userIdSalt: "pepper",
    fetch: fetchMock as unknown as typeof fetch,
  });
}

describe("PortfolioStore", () => {
  it("returns empty portfolio when key missing", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(makeResponse({ result: null }));
    const store = new PortfolioStore(makeStore(fetchMock));
    const p = await store.get(42);
    expect(p.chat_id).toBe(42);
    expect(p.holdings).toEqual([]);
  });

  it("portfolio key is hashed — raw chat_id never appears", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(makeResponse({ result: null }));
    const store = new PortfolioStore(makeStore(fetchMock));
    await store.get(8200970431);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body[0]).toBe("GET");
    expect(body[1]).toMatch(/^portfolio:[0-9a-f]{16}$/);
    expect(body[1]).not.toContain("8200970431");
  });

  it("add appends a holding and persists JSON", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makeResponse({ result: null })) // GET
      .mockResolvedValueOnce(makeResponse({ result: "OK" })); // SET
    const store = new PortfolioStore(makeStore(fetchMock));
    const holding = makeHolding({
      symbol: "RELIANCE.NS",
      qty: 50,
      buy_price: 2400,
      buy_date: "2026-04-21",
    });
    await store.add(42, holding);
    const setCall = fetchMock.mock.calls[1][1];
    const body = JSON.parse(setCall.body);
    expect(body[0]).toBe("SET");
    const payload = JSON.parse(body[2]);
    expect(payload.chat_id).toBe(42);
    expect(payload.holdings).toHaveLength(1);
    expect(payload.holdings[0].symbol).toBe("RELIANCE.NS");
    expect(payload.holdings[0].stop_loss).toBeCloseTo(2232, 2);
  });

  it("remove drops the matching symbol and writes back", async () => {
    const existing = JSON.stringify({
      chat_id: 42,
      holdings: [
        makeHolding({ symbol: "RELIANCE.NS", qty: 50, buy_price: 2400, buy_date: "2026-04-21" }),
        makeHolding({ symbol: "TCS.NS", qty: 10, buy_price: 3500, buy_date: "2026-04-22" }),
      ],
      cash_remaining: 0,
      last_updated: "2026-04-21T00:00:00Z",
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makeResponse({ result: existing }))
      .mockResolvedValueOnce(makeResponse({ result: "OK" }));
    const store = new PortfolioStore(makeStore(fetchMock));
    const removed = await store.remove(42, "RELIANCE.NS");
    expect(removed?.symbol).toBe("RELIANCE.NS");
    const setBody = JSON.parse(fetchMock.mock.calls[1][1].body);
    const payload = JSON.parse(setBody[2]);
    expect(payload.holdings).toHaveLength(1);
    expect(payload.holdings[0].symbol).toBe("TCS.NS");
  });

  it("remove returns null when symbol not present, no SET call", async () => {
    const existing = JSON.stringify({
      chat_id: 42,
      holdings: [],
      cash_remaining: 0,
      last_updated: "2026-04-21T00:00:00Z",
    });
    const fetchMock = vi.fn().mockResolvedValueOnce(makeResponse({ result: existing }));
    const store = new PortfolioStore(makeStore(fetchMock));
    const removed = await store.remove(42, "GHOST.NS");
    expect(removed).toBeNull();
    expect(fetchMock.mock.calls).toHaveLength(1);
  });

  it("clear writes an empty portfolio", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(makeResponse({ result: "OK" }));
    const store = new PortfolioStore(makeStore(fetchMock));
    await store.clear(42);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body[0]).toBe("SET");
    expect(JSON.parse(body[2]).holdings).toEqual([]);
  });

  it("corrupt JSON resets to empty portfolio without raising", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(makeResponse({ result: "not-json-{" }));
    const store = new PortfolioStore(makeStore(fetchMock));
    const p = await store.get(42);
    expect(p.holdings).toEqual([]);
  });
});

describe("readPicksCache", () => {
  it("returns null when key missing", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(makeResponse({ result: null }));
    expect(await readPicksCache(makeStore(fetchMock))).toBeNull();
  });

  it("parses cached picks payload", async () => {
    const payload = JSON.stringify({
      picks: [
        {
          symbol: "RELIANCE.NS",
          composite_rating: 91,
          endorsement_count: 2,
          endorsing_codes: ["canslim", "schloss"],
          fundamentals_summary: "px=₹2520",
        },
      ],
      computed_at: "2026-04-27T03:00:00Z",
    });
    const fetchMock = vi.fn().mockResolvedValueOnce(makeResponse({ result: payload }));
    const cached = await readPicksCache(makeStore(fetchMock));
    expect(cached).not.toBeNull();
    expect(cached?.picks[0].symbol).toBe("RELIANCE.NS");
  });

  it("returns null on corrupt JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(makeResponse({ result: "garbage{" }));
    expect(await readPicksCache(makeStore(fetchMock))).toBeNull();
  });
});
