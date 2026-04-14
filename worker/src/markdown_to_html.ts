/**
 * Convert Gemini's Markdown-ish output to Telegram-safe HTML.
 *
 * Ported from bot/markdown_to_html.py — see that file for the design
 * rationale. Telegram's HTML parse mode only accepts <b>, <i>, <u>, <s>,
 * <code>, <pre>, <a href="…">. Anything else causes a 400.
 *
 * Processing order is important:
 * 1. Escape <, >, & first (all user-visible angle brackets are unsafe).
 * 2. Resolve bullets + bold + italic BEFORE heading wrap, so `## **X**`
 *    does not become `<b><b>X</b></b>` which Telegram rejects.
 * 3. Heading wrap strips any redundant inner <b> the earlier pass produced.
 * 4. Unmatched ** / __ / * / _ stay as literal text — the stream may be
 *    mid-token and we cannot speculate about an eventual closer.
 *
 * Any regex failure falls back to plain HTML-escaped text rather than
 * throwing, so streaming never stalls on a bad input.
 */

const ESCAPE_MAP: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
};

function escape(text: string): string {
  let out = "";
  for (const ch of text) {
    out += ESCAPE_MAP[ch] ?? ch;
  }
  return out;
}

// `**x**` / `__x__` — non-greedy, inline only (no newlines), matcher
// requires the content to not start with the marker character so `****`
// does not match as an empty pair.
const BOLD_STAR = /\*\*([^\n*][^\n]*?)\*\*/g;
const BOLD_UND = /__([^\n_][^\n]*?)__/g;

// `*x*` italic — explicit negative look-around so it doesn't eat the
// asterisks inside already-processed **…** (those have been replaced
// with <b> by the time italics run).
const ITAL_STAR = /(?<!\*)\*(?!\*)([^\n*][^\n]*?)(?<!\*)\*(?!\*)/g;

// `_x_` italic with WORD boundaries — prevents mangling identifiers like
// NSE_RELIANCE_EQ. Open `_` must be preceded by start/space/punct; close
// `_` must be followed by space/punct/end.
const ITAL_UND = /(^|[\s\W])_(?!_)([^\n_]+?)_(?!_)(?=[\s\W]|$)/g;

// Leading bullet markers.
const BULLET_LINE = /^[ \t]*[*\-]\s+/gm;

// Headings — wrap the entire line.
const HEADING_LINE = /^[ \t]*#{1,6}\s+(.+)$/gm;

function wrapBold(_match: string, inner: string): string {
  if (!inner.trim()) return _match;
  return `<b>${inner}</b>`;
}

function wrapItalicAnchored(_match: string, lead: string, inner: string): string {
  if (!inner.trim()) return _match;
  return `${lead}<i>${inner}</i>`;
}

function wrapItalicUnanchored(_match: string, inner: string): string {
  if (!inner.trim()) return _match;
  return `<i>${inner}</i>`;
}

function wrapHeading(_match: string, inner: string): string {
  if (!inner.trim()) return _match;
  // Strip any inner <b> the earlier pass produced — the heading will
  // bold the whole line and nested <b><b> is rejected by Telegram.
  const cleaned = inner.replace(/<\/?b>/g, "");
  return `<b>${cleaned}</b>`;
}

export function markdownToHtml(text: string): string {
  try {
    let out = escape(text);
    // Bullets + bold + italic BEFORE heading wrap.
    out = out.replace(BULLET_LINE, "• ");
    out = out.replace(BOLD_STAR, wrapBold);
    out = out.replace(BOLD_UND, wrapBold);
    out = out.replace(ITAL_STAR, wrapItalicUnanchored);
    out = out.replace(ITAL_UND, wrapItalicAnchored);
    out = out.replace(HEADING_LINE, wrapHeading);
    return out;
  } catch (exc) {
    // Regex backtracking on pathological input; fall back to escape-only.
    console.warn("markdownToHtml degraded to escape-only:", exc);
    return escape(text);
  }
}
