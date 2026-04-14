"""Convert Gemini's Markdown-ish output to Telegram-safe HTML.

Telegram's HTML parse mode accepts a small allowlist:
  <b> <i> <u> <s> <code> <pre> <a href>

Any other tags cause a 400 "can't parse entities". That means we must:

1. Escape raw ``<``, ``>``, ``&`` in the input (they are untrusted content
   from Gemini — could contain tickers written as ``<X>``).
2. Then convert **matched** Markdown features to allowlisted tags.
3. Leave **unmatched** Markdown characters (e.g. a lone ``**`` during
   streaming, or odd nested asterisks) as literal text.

This is called on every streaming edit, so partial / mid-sentence
content must not explode — unmatched tags would reject the whole edit.

Processing order is important:
- Bullets + bold + italic first so a heading like ``## **Title**`` gets
  its inner markdown resolved before the heading line is wrapped.
  Otherwise we would emit ``<b><b>Title</b></b>`` which Telegram rejects.
- Italic underscore requires word-boundary context so tickers like
  ``NSE_RELIANCE_EQ`` don't get mangled into ``NSE<i>RELIANCE</i>EQ``.
- Any regex failure, recursion, or memory surge is caught and degrades
  to a plain HTML-escaped return so streaming never stalls on a bad
  input.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Bold — `**x**` and `__x__`. Both sides must be the marker; non-greedy so
# one unmatched ** can't swallow the rest of the paragraph.
_BOLD_STAR = re.compile(r"\*\*(?P<txt>[^\n*][^\n]*?)\*\*", re.MULTILINE)
_BOLD_UND = re.compile(r"__(?P<txt>[^\n_][^\n]*?)__", re.MULTILINE)

# Italic asterisks. Require that the surrounding characters are NOT also
# `*` (so we don't match the middle of `**...**`). Already-handled bold
# regions do not appear here because we run bold first.
_ITAL_STAR = re.compile(
    r"(?<!\*)\*(?!\*)(?P<txt>[^\n*][^\n]*?)(?<!\*)\*(?!\*)",
    re.MULTILINE,
)

# Italic underscores need tighter boundaries: the opening `_` must be
# preceded by start-of-string, whitespace, or punctuation, and the
# closing `_` must be followed by the same. Prevents mangling
# identifier-like tokens (``NSE_RELIANCE_EQ``, ``q3_results.pdf``).
_ITAL_UND = re.compile(
    r"(?:(?<=^)|(?<=[\s\W]))_(?!_)(?P<txt>[^\n_]+?)_(?!_)(?=[\s\W]|$)",
    re.MULTILINE,
)

# Bullet markers at the start of a line.
_BULLET_LINE = re.compile(r"(?m)^[ \t]*[*\-]\s+")

# Heading. Wraps the entire line after any inner markdown has been
# resolved (we run bold/italic BEFORE the heading replacer).
_HEADING_LINE = re.compile(r"(?m)^[ \t]*#{1,6}\s+(?P<txt>.+)$")

_ESCAPE_MAP = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
}


def _escape(text: str) -> str:
    out = []
    for ch in text:
        out.append(_ESCAPE_MAP.get(ch, ch))
    return "".join(out)


def _wrap_bold(match: re.Match) -> str:
    inner = match.group("txt")
    if not inner.strip():
        return match.group(0)  # leave `****` or `__ __` as literal
    return f"<b>{inner}</b>"


def _wrap_italic(match: re.Match) -> str:
    inner = match.group("txt")
    if not inner.strip():
        return match.group(0)
    return f"<i>{inner}</i>"


def _wrap_heading(match: re.Match) -> str:
    inner = match.group("txt")
    if not inner.strip():
        return match.group(0)
    # Earlier passes may have wrapped the inner text in <b>...</b>. The
    # heading will bold the whole line anyway, so strip the redundant
    # inner tags to avoid <b><b>Title</b></b> — which Telegram rejects.
    inner = inner.replace("<b>", "").replace("</b>", "")
    return f"<b>{inner}</b>"


def markdown_to_html(text: str) -> str:
    """Convert a subset of Markdown to Telegram-safe HTML.

    On any unexpected regex / recursion failure the function logs and
    returns an HTML-escaped copy of the raw input so the streaming
    adapter can keep going.
    """
    try:
        escaped = _escape(text)
        # Bullets + bold + italic MUST run before heading wrap so
        # "## **Title**" does not become nested <b><b>Title</b></b>.
        escaped = _BULLET_LINE.sub("• ", escaped)
        escaped = _BOLD_STAR.sub(_wrap_bold, escaped)
        escaped = _BOLD_UND.sub(_wrap_bold, escaped)
        escaped = _ITAL_STAR.sub(_wrap_italic, escaped)
        escaped = _ITAL_UND.sub(_wrap_italic, escaped)
        escaped = _HEADING_LINE.sub(_wrap_heading, escaped)
        return escaped
    except (re.error, RecursionError, MemoryError) as exc:
        logger.warning(
            "markdown_to_html degrade-to-escape on %d-char input: %s",
            len(text),
            exc,
        )
        return _escape(text)
