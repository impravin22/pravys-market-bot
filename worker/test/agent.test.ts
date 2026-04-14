import { describe, expect, it } from "vitest";
import { SYSTEM_INSTRUCTION, normaliseHistoryRole } from "../src/agent";

describe("SYSTEM_INSTRUCTION", () => {
  it("contains the CAN SLIM opener, Pravy sign-off, British voice mandate", () => {
    expect(SYSTEM_INSTRUCTION).toContain("According to Pravy's CAN SLIM philosophy");
    expect(SYSTEM_INSTRUCTION).toContain(
      "This is how Pravy thinks — take it or leave it, mate.",
    );
    expect(SYSTEM_INSTRUCTION).toContain("British English");
    expect(SYSTEM_INSTRUCTION).toContain("mate");
  });

  it("cites risk rules and thresholds", () => {
    expect(SYSTEM_INSTRUCTION).toContain("7–8%");
    expect(SYSTEM_INSTRUCTION).toContain("20–25%");
    expect(SYSTEM_INSTRUCTION).toContain("RS ≥ 80");
  });

  it("bans compliance disclaimers by name inside a BANNED LINES section", () => {
    expect(SYSTEM_INSTRUCTION).toContain("BANNED LINES");
    const banned = SYSTEM_INSTRUCTION.split("BANNED LINES")[1];
    expect(banned).toContain("Educational signals, not investment advice.");
    expect(banned).toContain("Do your own research.");
    expect(banned).toContain("I am not a financial adviser.");
  });

  it("mandates grounding and the 'from memory, unverified' fallback", () => {
    expect(SYSTEM_INSTRUCTION).toContain("Use Google Search for every numeric claim");
    expect(SYSTEM_INSTRUCTION).toContain("from memory, unverified");
    expect(SYSTEM_INSTRUCTION).toContain("Never guess, never interpolate");
  });

  it("asks for double-asterisk bold + ₹ glyph for INR", () => {
    expect(SYSTEM_INSTRUCTION).toContain("double-asterisks");
    expect(SYSTEM_INSTRUCTION).toContain("₹");
  });
});

describe("normaliseHistoryRole", () => {
  it("maps user / model through", () => {
    expect(normaliseHistoryRole("user")).toBe("user");
    expect(normaliseHistoryRole("model")).toBe("model");
  });

  it("maps assistant / bot to model", () => {
    expect(normaliseHistoryRole("assistant")).toBe("model");
    expect(normaliseHistoryRole("BOT")).toBe("model");
  });

  it("drops system and unknown roles", () => {
    expect(normaliseHistoryRole("system")).toBeNull();
    expect(normaliseHistoryRole("tool")).toBeNull();
  });

  it("null / missing defaults to user", () => {
    expect(normaliseHistoryRole(null)).toBe("user");
    expect(normaliseHistoryRole(undefined)).toBe("user");
  });
});
