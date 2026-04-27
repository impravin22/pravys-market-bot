"""screener.in historical-tables parser + as-of helper tests."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from core.data.screener_history import (
    HistoricalFundamentals,
    compute_eps_cagr_pct,
    compute_roe_avg_pct,
    count_positive_years,
    fetch_history,
    historical_fundamentals_at,
    parse_section_table,
    parse_year_label,
)

# -----------------------------------------------------------------------------
# Fixture: minimal screener.in-style HTML with all five history sections
# -----------------------------------------------------------------------------


FIXTURE = """
<html>
<body>

<section id="profit-loss" class="card">
  <h2>Profit &amp; Loss</h2>
  <table class="data-table">
    <thead>
      <tr><th></th><th>Mar 2020</th><th>Mar 2021</th><th>Mar 2022</th><th>Mar 2023</th><th>Mar 2024</th></tr>
    </thead>
    <tbody>
      <tr><td>Sales +</td><td>1,000</td><td>1,200</td><td>1,400</td><td>1,700</td><td>2,100</td></tr>
      <tr><td>Operating Profit</td><td>200</td><td>260</td><td>320</td><td>410</td><td>520</td></tr>
      <tr><td>EPS in Rs</td><td>10.0</td><td>12.5</td><td>15.0</td><td>18.5</td><td>22.0</td></tr>
    </tbody>
  </table>
</section>

<section id="balance-sheet" class="card">
  <h2>Balance Sheet</h2>
  <table class="data-table">
    <thead>
      <tr><th></th><th>Mar 2020</th><th>Mar 2021</th><th>Mar 2022</th><th>Mar 2023</th><th>Mar 2024</th></tr>
    </thead>
    <tbody>
      <tr><td>Borrowings</td><td>200</td><td>180</td><td>160</td><td>150</td><td>140</td></tr>
      <tr><td>Reserves</td><td>800</td><td>900</td><td>1,050</td><td>1,250</td><td>1,500</td></tr>
    </tbody>
  </table>
</section>

<section id="ratios" class="card">
  <h2>Ratios</h2>
  <table class="data-table">
    <thead>
      <tr><th></th><th>Mar 2020</th><th>Mar 2021</th><th>Mar 2022</th><th>Mar 2023</th><th>Mar 2024</th></tr>
    </thead>
    <tbody>
      <tr><td>ROE %</td><td>14 %</td><td>15 %</td><td>16 %</td><td>17 %</td><td>18 %</td></tr>
      <tr><td>ROCE %</td><td>15 %</td><td>16 %</td><td>17 %</td><td>18 %</td><td>19 %</td></tr>
      <tr><td>Debt / Equity</td><td>0.20</td><td>0.18</td><td>0.16</td><td>0.13</td><td>0.10</td></tr>
    </tbody>
  </table>
</section>

<section id="cash-flow" class="card">
  <h2>Cash Flows</h2>
  <table class="data-table">
    <thead>
      <tr><th></th><th>Mar 2020</th><th>Mar 2021</th><th>Mar 2022</th><th>Mar 2023</th><th>Mar 2024</th></tr>
    </thead>
    <tbody>
      <tr><td>Cash from Operating Activity</td><td>180</td><td>240</td><td>290</td><td>360</td><td>460</td></tr>
      <tr><td>Cash from Investing Activity</td><td>-100</td><td>-120</td><td>-140</td><td>-180</td><td>-200</td></tr>
    </tbody>
  </table>
</section>

<section id="quarters" class="card">
  <h2>Quarterly Results</h2>
  <table class="data-table">
    <thead>
      <tr><th></th><th>Mar 2024</th><th>Jun 2024</th><th>Sep 2024</th><th>Dec 2024</th></tr>
    </thead>
    <tbody>
      <tr><td>Sales +</td><td>500</td><td>520</td><td>550</td><td>600</td></tr>
      <tr><td>EPS in Rs</td><td>5.0</td><td>5.4</td><td>5.7</td><td>6.2</td></tr>
    </tbody>
  </table>
</section>

</body>
</html>
"""


# -----------------------------------------------------------------------------
# parse_year_label
# -----------------------------------------------------------------------------


def test_parse_year_label_extracts_year_from_mar_format():
    assert parse_year_label("Mar 2024") == 2024
    assert parse_year_label("Mar 2020") == 2020


def test_parse_year_label_handles_quarter_format():
    # Quarter labels are still tagged by the year suffix.
    assert parse_year_label("Dec 2024") == 2024


def test_parse_year_label_returns_none_on_garbage():
    assert parse_year_label("TTM") is None
    assert parse_year_label("") is None


# -----------------------------------------------------------------------------
# parse_section_table
# -----------------------------------------------------------------------------


def test_parse_section_table_returns_label_to_values():
    rows = parse_section_table(FIXTURE, "ratios")
    assert rows["headers"] == ["Mar 2020", "Mar 2021", "Mar 2022", "Mar 2023", "Mar 2024"]
    assert rows["ROE %"] == [14.0, 15.0, 16.0, 17.0, 18.0]
    assert rows["Debt / Equity"] == [0.20, 0.18, 0.16, 0.13, 0.10]


def test_parse_section_table_strips_label_decorations():
    rows = parse_section_table(FIXTURE, "profit-loss")
    # "Sales +" should be cleanly keyed under "Sales".
    assert "Sales" in rows
    assert rows["Sales"] == [1000.0, 1200.0, 1400.0, 1700.0, 2100.0]


def test_parse_section_table_missing_section_returns_empty():
    rows = parse_section_table("<html></html>", "ratios")
    assert rows == {}


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def test_compute_roe_avg_uses_last_n_non_none_values():
    assert compute_roe_avg_pct([14.0, 15.0, 16.0, 17.0, 18.0], n=5) == 16.0
    assert compute_roe_avg_pct([None, 15.0, 16.0, 17.0, 18.0], n=5) == 16.5


def test_compute_roe_avg_returns_none_when_empty():
    assert compute_roe_avg_pct([], n=5) is None
    assert compute_roe_avg_pct([None, None], n=5) is None


def test_count_positive_years():
    assert count_positive_years([10.0, 12.0, -5.0, 14.0, 18.0]) == 4
    assert count_positive_years([None, 10.0, None]) == 1


def test_compute_eps_cagr_pct():
    # 10 → 22 over 5 endpoints (4 periods) ≈ +21.7% CAGR.
    cagr = compute_eps_cagr_pct([10.0, 12.5, 15.0, 18.5, 22.0])
    assert cagr is not None
    assert 21.0 < cagr < 22.5


def test_compute_eps_cagr_pct_handles_negative_endpoints():
    assert compute_eps_cagr_pct([10.0, -5.0]) is None  # cannot CAGR through negative
    assert compute_eps_cagr_pct([0.0, 10.0]) is None


# -----------------------------------------------------------------------------
# HistoricalFundamentals
# -----------------------------------------------------------------------------


def test_historical_fundamentals_built_from_fixture():
    history = fetch_history("X.NS", html=FIXTURE)
    assert isinstance(history, HistoricalFundamentals)
    assert history.annual_years == [2020, 2021, 2022, 2023, 2024]
    assert history.annual_eps == [10.0, 12.5, 15.0, 18.5, 22.0]
    assert history.annual_roe_pct == [14.0, 15.0, 16.0, 17.0, 18.0]
    assert history.annual_revenue == [1000.0, 1200.0, 1400.0, 1700.0, 2100.0]
    assert history.annual_operating_cash_flow == [180.0, 240.0, 290.0, 360.0, 460.0]


def test_quarterly_eps_extracted():
    history = fetch_history("X.NS", html=FIXTURE)
    assert history.quarterly_eps == [5.0, 5.4, 5.7, 6.2]


# -----------------------------------------------------------------------------
# fetch_history (HTTP path mocked)
# -----------------------------------------------------------------------------


def _mock_resp(*, status: int = 200, body: str = FIXTURE) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = body
    return resp


def test_fetch_history_via_http_client():
    http = MagicMock()
    http.get.return_value = _mock_resp()
    history = fetch_history("X.NS", http_client=http)
    assert history is not None
    assert history.symbol == "X.NS"
    assert len(history.annual_eps) == 5


def test_fetch_history_returns_none_on_404():
    http = MagicMock()
    http.get.return_value = _mock_resp(status=404, body="not found")
    assert fetch_history("X.NS", http_client=http) is None


# -----------------------------------------------------------------------------
# historical_fundamentals_at
# -----------------------------------------------------------------------------


def _history() -> HistoricalFundamentals:
    return fetch_history("X.NS", html=FIXTURE)  # type: ignore[return-value]


def test_historical_fundamentals_at_returns_snapshot_for_year():
    snap = historical_fundamentals_at(_history(), as_of=date(2023, 6, 30))
    assert snap is not None
    # Latest annual on or before mid-2023 is FY ending Mar 2023.
    assert snap.symbol == "X.NS"
    assert snap.roe_5y_avg_pct is not None
    assert 14.0 <= snap.roe_5y_avg_pct <= 18.0


def test_historical_fundamentals_at_returns_none_for_pre_history():
    snap = historical_fundamentals_at(_history(), as_of=date(2010, 1, 1))
    assert snap is None


def test_historical_fundamentals_at_caps_to_latest_when_future_date():
    snap = historical_fundamentals_at(_history(), as_of=date(2099, 1, 1))
    assert snap is not None
    # Latest row is FY 2024 (Mar 2024 close); 5y ROE mean of 14..18 = 16.0.
    assert snap.roe_5y_avg_pct == 16.0
    assert snap.roce_pct == 19.0
    assert snap.debt_to_equity == 0.10
