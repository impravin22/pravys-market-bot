from bot.markdown_to_html import markdown_to_html


def test_escapes_raw_html():
    assert markdown_to_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_bold_asterisks():
    assert markdown_to_html("**Power Finance Corporation**") == ("<b>Power Finance Corporation</b>")


def test_bold_underscores():
    assert markdown_to_html("__Power Finance__") == "<b>Power Finance</b>"


def test_italic_asterisks():
    assert markdown_to_html("consider *selective* exposure") == (
        "consider <i>selective</i> exposure"
    )


def test_italic_underscores():
    assert markdown_to_html("hold _tight_ mate") == "hold <i>tight</i> mate"


def test_unmatched_bold_left_as_literal():
    out = markdown_to_html("**Power Finance Corp")
    assert "<b>" not in out
    assert "**" in out


def test_bullet_star_converts_to_bullet():
    assert markdown_to_html("* hello\n* world") == "• hello\n• world"


def test_bullet_dash_converts_to_bullet():
    assert markdown_to_html("- hello\n- world") == "• hello\n• world"


def test_bullets_preserve_inline_bold():
    assert markdown_to_html("* **PFC** is a PSU NBFC") == ("• <b>PFC</b> is a PSU NBFC")


def test_heading_renders_as_bold():
    assert markdown_to_html("## Power Finance Corporation") == ("<b>Power Finance Corporation</b>")


def test_heading_with_inner_bold_does_not_nest_tags():
    """Regression: `## **Title**` used to become `<b><b>Title</b></b>` — rejected by Telegram."""
    out = markdown_to_html("## **Power Finance**")
    # At most one <b> wrap — Telegram's HTML parser rejects <b><b>.
    assert out.count("<b>") == 1
    assert out.count("</b>") == 1
    assert "Power Finance" in out


def test_heading_with_internal_asterisks_has_balanced_tags():
    out = markdown_to_html("## Q3 picks: 5 * strong, 3 * watch")
    assert out.count("<b>") == out.count("</b>")
    assert out.count("<i>") == out.count("</i>")


def test_combined_paragraph():
    src = (
        "According to Pravy's CAN SLIM philosophy, here are the picks.\n"
        "\n"
        "**Power Finance Corporation (PFC)**\n"
        "* C - Q3 profit ₹8,211 cr (per the NSE filing).\n"
        "* A - 5Y EPS CAGR of 65.7%.\n"
    )
    out = markdown_to_html(src)
    assert "<b>Power Finance Corporation (PFC)</b>" in out
    assert "• C - Q3 profit ₹8,211 cr" in out
    assert "**" not in out


def test_bold_cannot_swallow_whole_message_across_paragraphs():
    src = "**ticker\n\nnew paragraph"
    out = markdown_to_html(src)
    assert "<b>" not in out
    assert "new paragraph" in out


def test_ampersand_inside_bold():
    assert markdown_to_html("**Procter & Gamble**") == ("<b>Procter &amp; Gamble</b>")


def test_angle_brackets_inside_prose_escaped():
    assert markdown_to_html("<script>alert(1)</script>") == (
        "&lt;script&gt;alert(1)&lt;/script&gt;"
    )


def test_underscores_inside_identifier_not_italicised():
    """Regression: NSE_RELIANCE_EQ used to render as NSE<i>RELIANCE</i>EQ."""
    assert markdown_to_html("watch NSE_RELIANCE_EQ for breakout") == (
        "watch NSE_RELIANCE_EQ for breakout"
    )


def test_filename_with_underscores_survives():
    assert markdown_to_html("see q3_results_final.pdf") == ("see q3_results_final.pdf")


def test_single_underscore_pair_still_italicises_in_prose():
    assert markdown_to_html("hold _tight_ mate") == "hold <i>tight</i> mate"


def test_empty_bold_quad_asterisk_does_not_emit_empty_tag():
    """Mid-stream `****` must not become `<b></b>` — Telegram rejects empty tags."""
    out = markdown_to_html("price is **** rupees")
    assert "<b></b>" not in out


def test_triple_asterisk_produces_valid_balanced_tags():
    out = markdown_to_html("***PFC***")
    assert out.count("<b>") == out.count("</b>")
    assert out.count("<i>") == out.count("</i>")
    assert "<b></b>" not in out
    assert "<i></i>" not in out


def test_extreme_input_returns_escaped_fallback_without_exception():
    """Pathological input must not raise — degrade to escape-only output."""
    mess = "**" * 2000 + "\n" + "_" * 2000
    out = markdown_to_html(mess)
    # Whatever the regex engine does, we must get a string back.
    assert isinstance(out, str)


def test_markdown_link_kept_literal():
    """Links are currently passed through as literal Markdown. Lock the behaviour."""
    out = markdown_to_html("see [moneycontrol](https://www.moneycontrol.com/q)")
    # No <a> tag is emitted today (we may add it later; regression prevents
    # accidental partial rendering).
    assert "<a " not in out
    assert "[moneycontrol]" in out


def test_inline_backtick_kept_literal():
    assert markdown_to_html("use `head()`") == "use `head()`"


def test_fenced_code_block_kept_literal():
    out = markdown_to_html("```python\ncode()\n```")
    assert "<pre>" not in out
    assert "<code>" not in out
    assert "```" in out
