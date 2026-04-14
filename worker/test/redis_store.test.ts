import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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

describe("RedisStore", () => {
  let fetchMock: FetchMock;

  beforeEach(() => {
    fetchMock = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("rate limit key is hashed — raw user_id never appears", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ result: null }));
    const store = makeStore(fetchMock);
    await store.isRateLimited(42);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body[0]).toBe("GET");
    expect(body[1]).toMatch(/^rate_limit:[0-9a-f]{16}$/);
    expect(body[1]).not.toContain("42");
  });

  it("chat history key is hashed — raw chat_id never appears (DMs!)", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ result: null }));
    const store = makeStore(fetchMock);
    await store.getHistory(8200970431);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body[1]).toMatch(/^chat_history:[0-9a-f]{16}$/);
    expect(body[1]).not.toContain("8200970431");
  });

  it("isRateLimited returns true within window", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ result: new Date().toISOString() }));
    const store = makeStore(fetchMock);
    expect(await store.isRateLimited(42)).toBe(true);
  });

  it("isRateLimited returns false after window", async () => {
    const old = new Date(Date.now() - 120_000).toISOString();
    fetchMock.mockResolvedValueOnce(makeResponse({ result: old }));
    const store = makeStore(fetchMock);
    expect(await store.isRateLimited(42, 30)).toBe(false);
  });

  it("isRateLimited returns false on malformed timestamp", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ result: "garbage" }));
    const store = makeStore(fetchMock);
    expect(await store.isRateLimited(42)).toBe(false);
  });

  it("markUser sets with EX 30", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ result: "OK" }));
    const store = makeStore(fetchMock);
    await store.markUser(42);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body[0]).toBe("SET");
    expect(body[3]).toBe("EX");
    expect(body[4]).toBe("30");
  });

  it("getHistory returns [] on missing key", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ result: null }));
    const store = makeStore(fetchMock);
    expect(await store.getHistory(-100500)).toEqual([]);
  });

  it("getHistory parses JSON", async () => {
    const payload = JSON.stringify([
      { role: "user", text: "hi" },
      { role: "model", text: "all good" },
    ]);
    fetchMock.mockResolvedValueOnce(makeResponse({ result: payload }));
    const store = makeStore(fetchMock);
    expect(await store.getHistory(-100500)).toEqual([
      { role: "user", text: "hi" },
      { role: "model", text: "all good" },
    ]);
  });

  it("getHistory returns [] on corrupt JSON", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ result: "not json" }));
    const store = makeStore(fetchMock);
    expect(await store.getHistory(-100500)).toEqual([]);
  });

  it("appendTurn caps history at 2*limit entries", async () => {
    const old = Array.from({ length: 30 }, (_, i) => ({
      role: (i % 2 === 0 ? "user" : "model") as "user" | "model",
      text: `msg ${i}`,
    }));
    fetchMock
      .mockResolvedValueOnce(makeResponse({ result: JSON.stringify(old) }))
      .mockResolvedValueOnce(makeResponse({ result: "OK" }));
    const store = makeStore(fetchMock);
    await store.appendTurn(-100500, "new user", "new bot", 10);
    const setBody = JSON.parse(fetchMock.mock.calls[1][1].body);
    const stored = JSON.parse(setBody[2]);
    expect(stored.length).toBe(20);
    expect(stored[stored.length - 1].text).toBe("new bot");
    expect(stored[stored.length - 2].text).toBe("new user");
  });

  it("raises on Upstash error field", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ error: "WRONGTYPE" }));
    const store = makeStore(fetchMock);
    await expect(store.isRateLimited(42)).rejects.toThrow(/WRONGTYPE/);
  });

  it("raises on 5xx", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<html>502 bad</html>", { status: 502 }));
    const store = makeStore(fetchMock);
    await expect(store.isRateLimited(42)).rejects.toThrow(/5xx/);
  });

  it("raises on transport failure", async () => {
    fetchMock.mockRejectedValueOnce(new Error("DNS refused"));
    const store = makeStore(fetchMock);
    await expect(store.isRateLimited(42)).rejects.toThrow(/transport error/);
  });
});
