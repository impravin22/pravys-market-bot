import { describe, expect, it } from "vitest";
import { extractText } from "../src/index";

describe("extractText", () => {
  it("strips leading mention via entity offset", () => {
    const msg = {
      text: "@pravys_market_bot what should I buy?",
      entities: [{ type: "mention", offset: 0, length: 19 }],
      chat: { id: -1, type: "group" },
    };
    expect(extractText(msg, "pravys_market_bot")).toBe("what should I buy?");
  });

  it("preserves plain DM text", () => {
    const msg = { text: "give me the top 5", chat: { id: -1, type: "private" } };
    expect(extractText(msg, "pravys_market_bot")).toBe("give me the top 5");
  });

  it("slash command with @bot suffix keeps verb", () => {
    const msg = {
      text: "/today@pravys_market_bot RELIANCE",
      chat: { id: -1, type: "group" },
    };
    expect(extractText(msg, "pravys_market_bot")).toBe("/today RELIANCE");
  });

  it("bare slash command with @bot suffix keeps verb", () => {
    const msg = {
      text: "/start@pravys_market_bot",
      chat: { id: -1, type: "group" },
    };
    expect(extractText(msg, "pravys_market_bot")).toBe("/start");
  });

  it("returns null for empty / whitespace", () => {
    expect(extractText({ text: "", chat: { id: -1, type: "private" } }, "x")).toBeNull();
    expect(extractText({ text: "   ", chat: { id: -1, type: "private" } }, "x")).toBeNull();
  });

  it("mid-sentence mention is not stripped", () => {
    const msg = {
      text: "hey look at @pravys_market_bot here",
      entities: [{ type: "mention", offset: 12, length: 19 }],
      chat: { id: -1, type: "group" },
    };
    expect(extractText(msg, "pravys_market_bot")).toBe("hey look at @pravys_market_bot here");
  });
});
