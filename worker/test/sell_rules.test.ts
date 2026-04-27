import { describe, expect, it } from "vitest";
import { Holding, makeHolding } from "../src/portfolio";
import { evaluateHolding } from "../src/sell_rules";
import type { OhlcvBar } from "../src/yahoo";

function bars(closes: number[], volumes?: number[]): OhlcvBar[] {
  return closes.map((c, i) => ({
    timestamp: 1700000000 + i * 86400,
    open: c,
    high: c * 1.01,
    low: c * 0.99,
    close: c,
    volume: volumes?.[i] ?? 1_000_000,
  }));
}

function holding(buyPrice = 100, daysAgo = 30): Holding {
  const buyDate = new Date();
  buyDate.setUTCDate(buyDate.getUTCDate() - daysAgo);
  return makeHolding({
    symbol: "X.NS",
    qty: 10,
    buy_price: buyPrice,
    buy_date: buyDate.toISOString().slice(0, 10),
  });
}

describe("evaluateHolding", () => {
  it("7% stop fires when close ≤ 93% of buy_price", () => {
    const h = holding(100);
    const sig = evaluateHolding({
      holding: h,
      currentClose: 92.5,
      bars: bars(Array(60).fill(100).concat([92.5])),
    });
    expect(sig.severity).toBe("sell");
    expect(sig.rule).toBe("stop_loss_7pct");
  });

  it("does not fire 7% stop just above floor", () => {
    const h = holding(100);
    const sig = evaluateHolding({
      holding: h,
      currentClose: 94.0,
      bars: bars(Array(60).fill(100).concat([94.0])),
    });
    expect(sig.rule).not.toBe("stop_loss_7pct");
  });

  it("50-DMA breach on volume fires SELL", () => {
    const closes = Array(50).fill(100).concat([102, 95]);
    const vols = Array(51).fill(1_000_000).concat([1_800_000]);
    const sig = evaluateHolding({
      holding: holding(100),
      currentClose: 95,
      bars: bars(closes, vols),
    });
    expect(sig.severity).toBe("sell");
    expect(sig.rule).toBe("broke_50dma_on_volume");
  });

  it("50-DMA breach on light volume does not fire", () => {
    const closes = Array(50).fill(100).concat([102, 99]);
    const vols = Array(51).fill(1_000_000).concat([800_000]);
    const sig = evaluateHolding({
      holding: holding(100),
      currentClose: 99,
      bars: bars(closes, vols),
    });
    expect(sig.severity).not.toBe("sell");
  });

  it("climax top fires on +25% run + +6% spike on highest volume", () => {
    const ramp = Array.from({ length: 21 }, (_, i) => 100 + i * 1.5); // 100 → 130
    const final = ramp[ramp.length - 1] * 1.07; // +7% spike
    const closes = ramp.concat([final]);
    const vols = Array(21).fill(1_000_000).concat([3_000_000]);
    const sig = evaluateHolding({
      holding: holding(100, 21),
      currentClose: final,
      bars: bars(closes, vols),
    });
    expect(sig.rule).toBe("climax_top");
  });

  it("RS deterioration fires TRIM when entry ≥85, current <70", () => {
    const sig = evaluateHolding({
      holding: holding(100),
      currentClose: 100,
      bars: bars(Array(60).fill(100)),
      entryRs: 88,
      currentRs: 65,
    });
    expect(sig.severity).toBe("trim");
    expect(sig.rule).toBe("rs_deterioration");
  });

  it("8-week-rule fires TRIM for non-leader (RS<80) within 56d, +20% gain", () => {
    const sig = evaluateHolding({
      holding: holding(100, 28),
      currentClose: 122,
      bars: bars(Array(60).fill(100).concat([122])),
      currentRs: 75,
    });
    expect(sig.rule).toBe("eight_week_rule_non_leader");
  });

  it("8-week-rule does not fire for leader RS≥80", () => {
    const sig = evaluateHolding({
      holding: holding(100, 28),
      currentClose: 122,
      bars: bars(Array(60).fill(100).concat([122])),
      currentRs: 88,
    });
    expect(sig.rule).not.toBe("eight_week_rule_non_leader");
  });

  it("8-week-rule stays silent without RS data", () => {
    const sig = evaluateHolding({
      holding: holding(100, 28),
      currentClose: 122,
      bars: bars(Array(60).fill(100).concat([122])),
      currentRs: null,
    });
    expect(sig.rule).not.toBe("eight_week_rule_non_leader");
  });

  it("HOLD when nothing fires", () => {
    const sig = evaluateHolding({
      holding: holding(100),
      currentClose: 102,
      bars: bars([100, 101, 102, 101.5, 102.5]),
    });
    expect(sig.severity).toBe("hold");
  });

  it("7% stop wins over 50-DMA breach when both fire", () => {
    const closes = Array(50).fill(100).concat([102, 90]); // -10% breach + 50-DMA
    const vols = Array(51).fill(1_000_000).concat([2_000_000]);
    const sig = evaluateHolding({
      holding: holding(100),
      currentClose: 90,
      bars: bars(closes, vols),
    });
    expect(sig.rule).toBe("stop_loss_7pct");
  });

  it("short history does not crash", () => {
    const sig = evaluateHolding({
      holding: holding(100),
      currentClose: 101,
      bars: bars([100, 101]),
    });
    expect(sig.severity).toBe("hold");
  });
});
