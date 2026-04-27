/**
 * Gemini 2.5 Pro agent — TS port of bot/agent.py.
 *
 * Differences from the Python version:
 *  - The CAN SLIM playbook PDF is referenced by Gemini Files API `file_id`
 *    instead of being uploaded from disk (Workers have no filesystem).
 *    The `file_id` is produced once by `scripts/upload_playbook.py` and
 *    stored as a Worker var.
 *  - Conversation history is passed in verbatim (same shape as the
 *    Python side) — the Worker resolves the history from Upstash before
 *    calling the agent.
 *
 * Uses @google/genai — the official JS SDK, which supports:
 *  - `google_search` as a first-class Tool.
 *  - `generateContentStream` for token-by-token streaming.
 *  - File references via `{ fileData: { fileUri, mimeType } }` parts.
 */

import { GoogleGenAI } from "@google/genai";

export const SYSTEM_INSTRUCTION = `You are Pravy's Market Bot. Think of yourself as a
sharp mate of Pravy's who reads the CAN SLIM playbook, watches the Indian
markets, and talks like a friend — not a compliance form. You have the
CAN SLIM methodology playbook as a reference document and Google Search
enabled for live fundamentals, prices, and news on NSE / BSE stocks.

VOICE (non-negotiable):
- British English only. "analyse", "realise", "colour", "organisation",
  "behaviour", "favourite". Never the American spellings.
- Call the user "mate". Drop it in naturally — "right mate", "listen
  mate", "here's the thing, mate". Not every sentence, just often enough
  that the voice lands.
- Be conversational, direct, a touch cheeky. Short sentences. Opinions
  are fine. You're a friend who reads the playbook, not a disclaimer.

HOW TO HANDLE DIFFERENT MESSAGES:
1. Banter and greetings ("you alright?", "what's up", "hey mate"):
   Reply with one short casual line in kind, like a mate would.
   Example: "All good mate, markets open in a bit — what's on your mind?"
   Do NOT open with "According to Pravy's CAN SLIM philosophy" for these.
   Do NOT sign off with the Pravy line for these.
2. Market / stock questions (picks, CAN SLIM scores, news on a ticker,
   regime check, commodity questions):
   Open with: "According to Pravy's CAN SLIM philosophy, …"
   Walk through the seven letters (C, A, N, S, L, I, M) using live data
   from Search. Cite the thresholds from the playbook: ≥25% quarterly EPS
   growth, ≥20% three-year EPS CAGR, within ~15% of 52-week high, ≥40%
   volume surge, RS ≥ 80, FII/DII net positive, confirmed uptrend.
   Mention Pravy's risk rules when giving picks: 7–8% stop-loss,
   20–25% profit-take, 6–8 positions max, average up not down.
   Close with exactly: "This is how Pravy thinks — take it or leave it, mate."
3. Off-topic asks (anything that isn't stocks / markets / CAN SLIM —
   politics, sports, relationships, philosophy, random facts):
   Politely shut it down, with warmth. Example style:
     "I'll tell you what, mate — let's keep this to stocks, eh?"
     "Nah mate, stocks only here — what ticker is on your mind?"
   Pick whichever phrasing fits the question. Do not explain, do not
   lecture. One line is enough.

GROUNDING (critical — Pravy hates made-up numbers):
- Use Google Search for every numeric claim: EPS, revenue, 52-week high,
  FII/DII stake, volume, RS, market cap, news. After you use Search,
  attribute the number inline to the real source that came back.
  Do NOT parrot the example phrases in this prompt; cite whatever
  Search actually returned.
- If you did NOT call Search for a number — because you answered from
  training memory — write "from memory, unverified" next to that
  number instead of inventing a source. Never claim a source you
  didn't actually see.
- If Search returns nothing or the data conflicts, say so plainly:
  "I couldn't verify the latest EPS for this one, mate — skipping that
  letter." Never guess, never interpolate.
- Every pick must open with a one-line WHY summary before the
  seven-letter walk-through: "Why it fits — strong Q3 beat, fresh
  52-week high, institutions buying." The letters then fill in the
  numbers with their sources.
- State actual values and whether they clear the playbook bar.

BANNED LINES:
- "Educational signals, not investment advice."
- "Do your own research."
- "I am not a financial adviser."

PANEL DATA INTEGRATION:
- When the user message is preceded by a "[PANEL: …]" block, that block
  is the authoritative seven-guru reading from Pravy's screening pipeline
  (O'Neil CAN SLIM, O'Shaughnessy Trending Value, Greenblatt Magic
  Formula, Graham Defensive, Buffett Lite, Lynch GARP, Walter Schloss).
- When panel data is present, weave it into your reply naturally. Quote
  the composite rating, list which gurus endorse and which fail, and
  expand 1-2 of the most informative checks. The panel data is sourced
  from screener.in + yfinance ratios, so trust those numbers; you can
  still use Search for breaking news and recent price action.
- If a stock has no PANEL block, it is not in today's universe — answer
  using Search alone and say so plainly.

FORMATTING (mandatory):
- The reply is rendered as Telegram HTML via a converter. Use
  **double-asterisks** around company names and tickers; the renderer
  turns them into bold. Use "* " or "- " at the start of a line for
  bullets.
- Never emit raw "<" or ">" in prose — write INR as ₹, percentage as %.
- Short lines. One idea per bullet.
`;

export interface HistoryTurn {
  role: "user" | "model";
  text: string;
}

export interface AgentConfig {
  apiKey: string;
  model: string;
  playbookFileId?: string;
  systemInstruction?: string;
}

const RETRY_ATTEMPTS = 3;
const RETRY_BACKOFFS_MS = [1500, 3000];

export type GeminiErrorKind =
  | "retryable"
  | "playbook_expired"
  | "auth"
  | "quota"
  | "content"
  | "unknown";

export function classifyGeminiError(exc: Error): GeminiErrorKind {
  const msg = exc.message.toLowerCase();
  if (
    msg.includes("file") &&
    (msg.includes("expired") ||
      msg.includes("not found") ||
      msg.includes("failed state") ||
      msg.includes("permission denied"))
  ) {
    return "playbook_expired";
  }
  if (
    msg.includes("api key") ||
    msg.includes("api_key") ||
    msg.includes("401") ||
    msg.includes("permission_denied") ||
    msg.includes("403")
  ) {
    return "auth";
  }
  if (msg.includes("429") || msg.includes("quota") || msg.includes("resource_exhausted")) {
    return "quota";
  }
  if (msg.includes("token count") || msg.includes("invalid_argument")) {
    return "content";
  }
  if (msg.includes("503") && (msg.includes("unavailable") || msg.includes("overloaded") || msg.includes("demand"))) {
    return "retryable";
  }
  if (msg.includes("502") || msg.includes("504") || msg.includes("timeout")) {
    return "retryable";
  }
  return "unknown";
}

function fallbackMessageFor(kind: GeminiErrorKind): string {
  switch (kind) {
    case "playbook_expired":
      return (
        "Right mate, the CAN SLIM playbook file expired on my side — " +
        "tell Pravy to re-run `scripts/upload_playbook.py`. I'll keep " +
        "going from memory in the meantime."
      );
    case "auth":
      return "⚠️ My Google API key is knackered, mate — Pravy needs to rotate it. Bot is down until then.";
    case "quota":
      return "I've hit today's Gemini quota, mate. Try again tomorrow, or tag Pravy to bump the limits.";
    case "content":
      return "Our conversation got too long for me to process — try /reset to clear and start fresh.";
    default:
      return "Sorry — I hit a snag fetching the market data right now. Try again in a minute.";
  }
}

export function normaliseHistoryRole(role: string | undefined | null): "user" | "model" | null {
  if (role == null) return "user";
  const n = role.trim().toLowerCase();
  if (n === "" || n === "user") return "user";
  if (n === "model" || n === "assistant" || n === "bot") return "model";
  return null; // system / tool / unknown → drop
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class HermesAgent {
  private readonly client: GoogleGenAI;

  constructor(private readonly config: AgentConfig) {
    this.client = new GoogleGenAI({ apiKey: config.apiKey });
  }

  async *streamReply(userMessage: string, history: HistoryTurn[] = []): AsyncGenerator<string> {
    const contents: Array<Record<string, unknown>> = [];
    for (const turn of history) {
      const role = normaliseHistoryRole(turn.role);
      if (!role) continue;
      if (!turn.text) continue;
      contents.push({ role, parts: [{ text: turn.text }] });
    }
    const currentParts: Array<Record<string, unknown>> = [{ text: userMessage }];
    if (this.config.playbookFileId) {
      currentParts.push({
        fileData: {
          fileUri: this.config.playbookFileId,
          mimeType: "application/pdf",
        },
      });
    }
    contents.push({ role: "user", parts: currentParts });

    const systemInstruction = this.config.systemInstruction ?? SYSTEM_INSTRUCTION;

    let lastError: Error | null = null;
    let droppedPlaybook = false;
    for (let attempt = 1; attempt <= RETRY_ATTEMPTS; attempt++) {
      try {
        const stream = await this.client.models.generateContentStream({
          model: this.config.model,
          contents,
          config: {
            systemInstruction,
            tools: [{ googleSearch: {} }],
          },
        });
        let emittedAny = false;
        for await (const chunk of stream) {
          const text = chunk?.text ?? "";
          if (text) {
            emittedAny = true;
            yield text;
          }
        }
        if (!emittedAny) {
          yield "I'm not sure — can you rephrase the question?";
        }
        return;
      } catch (exc) {
        lastError = exc as Error;
        const kind = classifyGeminiError(lastError);
        // Special recovery: if the CAN SLIM playbook URI expired, drop it
        // from the next attempt's contents and retry without the PDF.
        if (kind === "playbook_expired" && !droppedPlaybook) {
          console.error(
            "Gemini reports CAN SLIM playbook URI expired; retrying without PDF context:",
            lastError.message,
          );
          for (const part of currentParts) {
            if ("fileData" in part) {
              const idx = currentParts.indexOf(part);
              if (idx !== -1) currentParts.splice(idx, 1);
            }
          }
          droppedPlaybook = true;
          continue;
        }
        if (attempt < RETRY_ATTEMPTS && kind === "retryable") {
          const backoff = RETRY_BACKOFFS_MS[Math.min(attempt - 1, RETRY_BACKOFFS_MS.length - 1)];
          console.warn(
            `Gemini transient failure attempt ${attempt}/${RETRY_ATTEMPTS} (${kind}):`,
            lastError.message,
            `— retrying in ${backoff}ms`,
          );
          await sleep(backoff);
          continue;
        }
        console.error(`[gemini:${kind}] ${lastError.message}`);
        yield fallbackMessageFor(kind);
        return;
      }
    }
    const finalKind = lastError ? classifyGeminiError(lastError) : "unknown";
    console.error(`[gemini:${finalKind}] retries exhausted: ${lastError?.message}`);
    yield fallbackMessageFor(finalKind);
  }
}
