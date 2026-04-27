/**
 * O'Neil sell-rule engine — TS port of `core/sell_signals.py`.
 *
 * Same rule set, same priority order: 7% stop / pivot 8% stop / 50-DMA
 * breach on volume / climax top / 8-week-rule / RS deterioration.
 *
 * Operates on end-of-day OHLCV bars (from Yahoo) and a `Holding` record
 * (from Upstash). Returns the highest-priority signal, or HOLD if nothing
 * fires.
 */

import type { Holding } from "./portfolio";
import type { OhlcvBar } from "./yahoo";

export type SellSeverity = "sell" | "trim" | "watch" | "hold";

export interface SellSignal {
  severity: SellSeverity;
  rule: string;
  reason: string;
}

const DEFAULT_STOP_PCT = 0.07;
const PIVOT_STOP_PCT = 0.08;
const DMA_VOLUME_MULTIPLIER = 1.4;
const CLIMAX_GAIN_PCT = 25.0;
const CLIMAX_DAILY_SPIKE_PCT = 6.0;
const CLIMAX_LOOKBACK = 21;
const EIGHT_WEEKS_DAYS = 56;
const EIGHT_WEEK_GAIN_PCT = 20.0;
const LEADER_RS_THRESHOLD = 80.0;
const RS_DETERIORATION_FROM = 85.0;
const RS_DETERIORATION_TO = 70.0;

const RULE_ORDER = [
  "stop_loss_7pct",
  "stop_loss_pivot_8pct",
  "broke_50dma_on_volume",
  "climax_top",
  "eight_week_rule_non_leader",
  "rs_deterioration",
];

export interface EvaluateInput {
  holding: Holding;
  currentClose: number;
  bars: OhlcvBar[];
  today?: Date;
  currentRs?: number | null;
  entryRs?: number | null;
}

export function evaluateHolding(input: EvaluateInput): SellSignal {
  const { holding, currentClose, bars } = input;
  const today = input.today ?? new Date();
  const closes = bars.map((b) => b.close).filter((c): c is number => typeof c === "number");
  const volumes = bars.map((b) => b.volume).filter((v): v is number => typeof v === "number");

  const candidates: SellSignal[] = [];
  let s: SellSignal | null;
  if ((s = stop7pct(holding, currentClose))) candidates.push(s);
  if ((s = stopPivot8pct(holding, currentClose))) candidates.push(s);
  if ((s = broke50dmaOnVolume(closes, volumes, currentClose))) candidates.push(s);
  if ((s = climaxTop(closes, volumes, currentClose))) candidates.push(s);
  if ((s = eightWeekRule(holding, currentClose, today, input.currentRs ?? null))) candidates.push(s);
  if ((s = rsDeterioration(input.entryRs ?? null, input.currentRs ?? null))) candidates.push(s);

  if (candidates.length === 0) {
    return { severity: "hold", rule: "hold", reason: "no sell rule triggered" };
  }
  candidates.sort((a, b) => RULE_ORDER.indexOf(a.rule) - RULE_ORDER.indexOf(b.rule));
  return candidates[0];
}

// -----------------------------------------------------------------------------

function stop7pct(holding: Holding, currentClose: number): SellSignal | null {
  const floor = holding.buy_price * (1.0 - DEFAULT_STOP_PCT);
  if (currentClose <= floor) {
    return {
      severity: "sell",
      rule: "stop_loss_7pct",
      reason:
        `closed ₹${currentClose.toFixed(2)} ≤ ₹${floor.toFixed(2)} ` +
        `(7% defensive stop from ₹${holding.buy_price.toFixed(2)})`,
    };
  }
  return null;
}

function stopPivot8pct(holding: Holding, currentClose: number): SellSignal | null {
  if (holding.pivot_price == null) return null;
  const floor = holding.pivot_price * (1.0 - PIVOT_STOP_PCT);
  if (currentClose <= floor) {
    return {
      severity: "sell",
      rule: "stop_loss_pivot_8pct",
      reason: `closed ₹${currentClose.toFixed(2)} ≤ ₹${floor.toFixed(2)} (8% below pivot ₹${holding.pivot_price.toFixed(2)})`,
    };
  }
  return null;
}

function broke50dmaOnVolume(
  closes: number[],
  volumes: number[],
  currentClose: number,
): SellSignal | null {
  if (closes.length < 51 || volumes.length < 50) return null;
  const dma50 = average(closes.slice(-50));
  const prevClose = closes[closes.length - 2];
  const avgVol50 = average(volumes.slice(-50));
  const lastVol = volumes[volumes.length - 1];
  if (
    currentClose < dma50 &&
    prevClose >= dma50 &&
    avgVol50 > 0 &&
    lastVol >= avgVol50 * DMA_VOLUME_MULTIPLIER
  ) {
    return {
      severity: "sell",
      rule: "broke_50dma_on_volume",
      reason:
        `closed ₹${currentClose.toFixed(2)} below 50-DMA ₹${dma50.toFixed(2)} on ` +
        `${(lastVol / avgVol50).toFixed(1)}x avg volume`,
    };
  }
  return null;
}

function climaxTop(
  closes: number[],
  volumes: number[],
  currentClose: number,
): SellSignal | null {
  if (closes.length < CLIMAX_LOOKBACK + 1) return null;
  const window = closes.slice(-(CLIMAX_LOOKBACK + 1));
  const start = window[0];
  if (start <= 0) return null;
  const gainPct = (currentClose / start - 1.0) * 100.0;
  if (gainPct < CLIMAX_GAIN_PCT) return null;
  const prevClose = closes[closes.length - 2];
  const dailySpikePct = (currentClose / prevClose - 1.0) * 100.0;
  if (dailySpikePct < CLIMAX_DAILY_SPIKE_PCT) return null;
  if (volumes.length >= CLIMAX_LOOKBACK + 1) {
    const recentVols = volumes.slice(-(CLIMAX_LOOKBACK + 1));
    const maxVol = Math.max(...recentVols);
    if (volumes[volumes.length - 1] < maxVol) return null;
  }
  return {
    severity: "sell",
    rule: "climax_top",
    reason:
      `+${gainPct.toFixed(1)}% in ${CLIMAX_LOOKBACK} sessions, ` +
      `+${dailySpikePct.toFixed(1)}% today on highest volume of run — exhaustion`,
  };
}

function eightWeekRule(
  holding: Holding,
  currentClose: number,
  today: Date,
  currentRs: number | null,
): SellSignal | null {
  const buy = new Date(holding.buy_date);
  const daysHeld = Math.floor((today.getTime() - buy.getTime()) / (1000 * 60 * 60 * 24));
  if (daysHeld < 0 || daysHeld > EIGHT_WEEKS_DAYS) return null;
  const gainPct = (currentClose / holding.buy_price - 1.0) * 100.0;
  if (gainPct < EIGHT_WEEK_GAIN_PCT) return null;
  // No RS data → can't classify leader vs non-leader. Stay silent.
  if (currentRs == null) return null;
  if (currentRs >= LEADER_RS_THRESHOLD) return null;
  return {
    severity: "trim",
    rule: "eight_week_rule_non_leader",
    reason:
      `+${gainPct.toFixed(1)}% in ${daysHeld}d but RS=${currentRs.toFixed(0)} ` +
      `(<${LEADER_RS_THRESHOLD.toFixed(0)}) — take profit on the non-leader`,
  };
}

function rsDeterioration(
  entryRs: number | null,
  currentRs: number | null,
): SellSignal | null {
  if (entryRs == null || currentRs == null) return null;
  if (entryRs >= RS_DETERIORATION_FROM && currentRs < RS_DETERIORATION_TO) {
    return {
      severity: "trim",
      rule: "rs_deterioration",
      reason: `RS dropped ${entryRs.toFixed(0)} → ${currentRs.toFixed(0)} since entry — leadership lost`,
    };
  }
  return null;
}

function average(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((a, b) => a + b, 0) / values.length;
}
