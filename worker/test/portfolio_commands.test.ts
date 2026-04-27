import { describe, expect, it, vi } from "vitest";
import { PortfolioStore } from "../src/portfolio";
import { PortfolioCommands, normaliseSymbol, parseCommand } from "../src/portfolio_commands";
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

function makeCommands(fetchMock: FetchMock, today = "2026-04-27"): PortfolioCommands {
  const redis = makeStore(fetchMock);
  return new PortfolioCommands(new PortfolioStore(redis), redis, () => today);
}

// -----------------------------------------------------------------------------
// parseCommand
// -----------------------------------------------------------------------------

describe("parseCommand", () => {
  it("returns null for non-command text", () => {
    expect(parseCommand("hello mate")).toBeNull();
    expect(parseCommand("")).toBeNull();
    expect(parseCommand("   ")).toBeNull();
  });

  it("extracts command and args", () => {
    expect(parseCommand("/add RELIANCE 50 2400")).toEqual({
      command: "add",
      args: ["RELIANCE", "50", "2400"],
    });
  });

  it("lowercases the command name", () => {
    expect(parseCommand("/PORTFOLIO")).toEqual({ command: "portfolio", args: [] });
  });

  it("collapses whitespace", () => {
    expect(parseCommand("  /add   X   10   100  ")).toEqual({
      command: "add",
      args: ["X", "10", "100"],
    });
  });
});

// -----------------------------------------------------------------------------
// normaliseSymbol
// -----------------------------------------------------------------------------

describe("normaliseSymbol", () => {
  it("auto-suffixes .NS when missing", () => {
    expect(normaliseSymbol("RELIANCE")).toBe("RELIANCE.NS");
  });
  it("preserves explicit .BO", () => {
    expect(normaliseSymbol("TCS.BO")).toBe("TCS.BO");
  });
  it("uppercases", () => {
    expect(normaliseSymbol("infy")).toBe("INFY.NS");
  });
});

// -----------------------------------------------------------------------------
// /help, unknown
// -----------------------------------------------------------------------------

describe("PortfolioCommands.handle", () => {
  it("/help returns the command list and skips agent", async () => {
    const fetchMock = vi.fn();
    const cmds = makeCommands(fetchMock);
    const out = await cmds.handle(1, "help", []);
    expect(out.shouldSkipAgent).toBe(true);
    expect(out.replyText).toContain("/portfolio");
    expect(out.replyText).toContain("/add");
  });

  it("unknown command falls through to agent", async () => {
    const fetchMock = vi.fn();
    const cmds = makeCommands(fetchMock);
    const out = await cmds.handle(1, "ticker", ["RELIANCE"]);
    expect(out.shouldSkipAgent).toBe(false);
  });
});

// -----------------------------------------------------------------------------
// /add
// -----------------------------------------------------------------------------

describe("/add", () => {
  it("with three args uses today as buy_date", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makeResponse({ result: null })) // GET
      .mockResolvedValueOnce(makeResponse({ result: "OK" })); // SET
    const cmds = makeCommands(fetchMock, "2026-04-27");
    const out = await cmds.handle(42, "add", ["RELIANCE", "50", "2400"]);
    expect(out.shouldSkipAgent).toBe(true);
    expect(out.replyText).toContain("Added RELIANCE.NS");
    const setBody = JSON.parse(fetchMock.mock.calls[1][1].body);
    const payload = JSON.parse(setBody[2]);
    expect(payload.holdings[0].buy_date).toBe("2026-04-27");
  });

  it("rejects non-integer qty", async () => {
    const fetchMock = vi.fn();
    const cmds = makeCommands(fetchMock);
    const out = await cmds.handle(1, "add", ["X", "abc", "100"]);
    expect(out.replyText.toLowerCase()).toContain("qty must be a whole number");
  });

  it("rejects non-positive qty", async () => {
    const fetchMock = vi.fn();
    const cmds = makeCommands(fetchMock);
    const out = await cmds.handle(1, "add", ["X", "-5", "100"]);
    expect(out.replyText.toLowerCase()).toContain("must be");
  });

  it("rejects malformed date", async () => {
    const fetchMock = vi.fn();
    const cmds = makeCommands(fetchMock);
    const out = await cmds.handle(1, "add", ["X", "10", "100", "2026/04/22"]);
    expect(out.replyText.toLowerCase()).toContain("yyyy-mm-dd");
  });

  it("rejects wrong arg count", async () => {
    const fetchMock = vi.fn();
    const cmds = makeCommands(fetchMock);
    const out = await cmds.handle(1, "add", ["X"]);
    expect(out.replyText.toLowerCase()).toContain("usage");
  });

  it("preserves explicit .BO suffix", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makeResponse({ result: null }))
      .mockResolvedValueOnce(makeResponse({ result: "OK" }));
    const cmds = makeCommands(fetchMock);
    await cmds.handle(1, "add", ["TCS.BO", "10", "3500"]);
    const setBody = JSON.parse(fetchMock.mock.calls[1][1].body);
    const payload = JSON.parse(setBody[2]);
    expect(payload.holdings[0].symbol).toBe("TCS.BO");
  });
});

// -----------------------------------------------------------------------------
// /remove
// -----------------------------------------------------------------------------

describe("/remove", () => {
  it("removes existing symbol", async () => {
    const existing = JSON.stringify({
      chat_id: 1,
      holdings: [
        {
          symbol: "RELIANCE.NS",
          qty: 50,
          buy_price: 2400,
          buy_date: "2026-04-21",
          stop_loss: 2232,
          notes: "",
        },
      ],
      cash_remaining: 0,
      last_updated: "2026-04-21T00:00:00Z",
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makeResponse({ result: existing }))
      .mockResolvedValueOnce(makeResponse({ result: "OK" }));
    const cmds = makeCommands(fetchMock);
    const out = await cmds.handle(1, "remove", ["RELIANCE"]);
    expect(out.replyText).toContain("Removed RELIANCE.NS");
  });

  it("friendly message when symbol unknown", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(makeResponse({ result: null }));
    const cmds = makeCommands(fetchMock);
    const out = await cmds.handle(1, "remove", ["GHOST"]);
    expect(out.replyText.toLowerCase()).toContain("not in your portfolio");
  });
});

// -----------------------------------------------------------------------------
// /clear
// -----------------------------------------------------------------------------

describe("/clear", () => {
  it("requires CONFIRM arg", async () => {
    const fetchMock = vi.fn();
    const cmds = makeCommands(fetchMock);
    const out = await cmds.handle(1, "clear", []);
    expect(out.replyText.toLowerCase()).toContain("confirm");
    expect(fetchMock.mock.calls).toHaveLength(0);
  });

  it("with CONFIRM clears", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(makeResponse({ result: "OK" }));
    const cmds = makeCommands(fetchMock);
    const out = await cmds.handle(1, "clear", ["CONFIRM"]);
    expect(out.replyText.toLowerCase()).toContain("cleared");
  });
});

// -----------------------------------------------------------------------------
// /portfolio
// -----------------------------------------------------------------------------

describe("/portfolio", () => {
  it("empty portfolio returns friendly message", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(makeResponse({ result: null }));
    const cmds = makeCommands(fetchMock);
    const out = await cmds.handle(1, "portfolio", []);
    expect(out.replyText.toLowerCase()).toContain("no holdings");
  });

  it("renders holdings with stop-loss", async () => {
    const existing = JSON.stringify({
      chat_id: 1,
      holdings: [
        {
          symbol: "RELIANCE.NS",
          qty: 50,
          buy_price: 2400,
          buy_date: "2026-04-21",
          stop_loss: 2232,
          notes: "",
        },
      ],
      cash_remaining: 0,
      last_updated: "2026-04-21T00:00:00Z",
    });
    const fetchMock = vi.fn().mockResolvedValueOnce(makeResponse({ result: existing }));
    const cmds = makeCommands(fetchMock);
    const out = await cmds.handle(1, "portfolio", []);
    expect(out.replyText).toContain("RELIANCE.NS");
    expect(out.replyText).toContain("2400");
    expect(out.replyText).toContain("Stop");
  });
});

// -----------------------------------------------------------------------------
// /picks
// -----------------------------------------------------------------------------

describe("/picks", () => {
  it("empty cache returns friendly message", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(makeResponse({ result: null }));
    const cmds = makeCommands(fetchMock);
    const out = await cmds.handle(1, "picks", []);
    expect(out.replyText.toLowerCase()).toContain("no picks");
  });

  it("renders cached picks", async () => {
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
    const cmds = makeCommands(fetchMock);
    const out = await cmds.handle(1, "picks", []);
    expect(out.replyText).toContain("RELIANCE.NS");
    expect(out.replyText).toContain("91");
    expect(out.replyText).toContain("canslim");
  });
});
