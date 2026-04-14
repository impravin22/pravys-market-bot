import { describe, expect, it } from "vitest";
import { markdownToHtml } from "../src/markdown_to_html";

describe("markdownToHtml", () => {
  it("escapes raw HTML", () => {
    expect(markdownToHtml("a < b & c > d")).toBe("a &lt; b &amp; c &gt; d");
  });

  it("bold asterisks", () => {
    expect(markdownToHtml("**Power Finance Corporation**")).toBe(
      "<b>Power Finance Corporation</b>",
    );
  });

  it("bold underscores", () => {
    expect(markdownToHtml("__Power Finance__")).toBe("<b>Power Finance</b>");
  });

  it("italic asterisks", () => {
    expect(markdownToHtml("consider *selective* exposure")).toBe(
      "consider <i>selective</i> exposure",
    );
  });

  it("italic underscores", () => {
    expect(markdownToHtml("hold _tight_ mate")).toBe("hold <i>tight</i> mate");
  });

  it("leaves unmatched bold as literal mid-stream", () => {
    const out = markdownToHtml("**Power Finance Corp");
    expect(out).not.toContain("<b>");
    expect(out).toContain("**");
  });

  it("star bullets become • bullets", () => {
    expect(markdownToHtml("* hello\n* world")).toBe("• hello\n• world");
  });

  it("dash bullets become • bullets", () => {
    expect(markdownToHtml("- hello\n- world")).toBe("• hello\n• world");
  });

  it("bullets preserve inline bold", () => {
    expect(markdownToHtml("* **PFC** is a PSU NBFC")).toBe(
      "• <b>PFC</b> is a PSU NBFC",
    );
  });

  it("heading renders as bold", () => {
    expect(markdownToHtml("## Power Finance Corporation")).toBe(
      "<b>Power Finance Corporation</b>",
    );
  });

  it("heading with inner bold does not nest <b><b>", () => {
    const out = markdownToHtml("## **Power Finance**");
    expect((out.match(/<b>/g) ?? []).length).toBe(1);
    expect((out.match(/<\/b>/g) ?? []).length).toBe(1);
  });

  it("underscores inside identifier not italicised", () => {
    expect(markdownToHtml("watch NSE_RELIANCE_EQ for breakout")).toBe(
      "watch NSE_RELIANCE_EQ for breakout",
    );
  });

  it("filename with underscores survives", () => {
    expect(markdownToHtml("see q3_results_final.pdf")).toBe("see q3_results_final.pdf");
  });

  it("ampersand inside bold escaped correctly", () => {
    expect(markdownToHtml("**Procter & Gamble**")).toBe("<b>Procter &amp; Gamble</b>");
  });

  it("script tags inside prose escaped", () => {
    expect(markdownToHtml("<script>alert(1)</script>")).toBe(
      "&lt;script&gt;alert(1)&lt;/script&gt;",
    );
  });

  it("empty quad-asterisk does not emit empty tag", () => {
    const out = markdownToHtml("price is **** rupees");
    expect(out).not.toContain("<b></b>");
  });

  it("markdown link kept literal (not yet converted to <a>)", () => {
    const out = markdownToHtml("see [moneycontrol](https://www.moneycontrol.com/q)");
    expect(out).not.toContain("<a ");
    expect(out).toContain("[moneycontrol]");
  });
});
